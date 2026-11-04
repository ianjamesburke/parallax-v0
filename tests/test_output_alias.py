"""#184: --output is an alias for --out on every command that exposes --out."""
from __future__ import annotations

import click
import pytest
import typer.main
from typer.testing import CliRunner

from parallax.cli import app


def _out_commands() -> list[str]:
    root = typer.main.get_command(app)
    found: list[str] = []

    def walk(cmd: click.Command, path: list[str]) -> None:
        if getattr(cmd, "commands", None):
            for name, sub in cmd.commands.items():
                walk(sub, path + [name])
            return
        for p in cmd.params:
            if getattr(p, "param_type_name", None) == "option" and "--out" in p.opts:
                found.append(" ".join(path))

    walk(root, [])
    return found


OUT_COMMANDS = _out_commands()


def test_found_out_commands():
    assert len(OUT_COMMANDS) >= 15


@pytest.mark.parametrize("cmd", OUT_COMMANDS)
def test_out_option_also_accepts_output(cmd):
    root = typer.main.get_command(app)
    node: click.Command = root
    for part in cmd.split():
        node = node.commands[part]  # type: ignore[attr-defined]
    opts = [p for p in node.params if getattr(p, "param_type_name", None) == "option" and "--out" in p.opts]
    assert len(opts) == 1
    assert "--output" in opts[0].opts


@pytest.mark.parametrize("flag", ["--out", "--output", "-o"])
def test_image_generate_output_alias_writes_file(tmp_path, monkeypatch, flag):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    monkeypatch.setenv("PARALLAX_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))
    target = tmp_path / "test.png"
    result = CliRunner().invoke(app, ["image", "generate", "a red apple", flag, str(target)])
    assert result.exit_code == 0, result.output
    assert target.is_file()
