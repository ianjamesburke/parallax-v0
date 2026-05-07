from __future__ import annotations

import shutil
import subprocess
import sys
from enum import Enum
from typing import Optional

import typer


completions_app = typer.Typer(
    help="Manage shell tab completion.",
    invoke_without_command=True,
    no_args_is_help=True,
)

verify_app = typer.Typer(
    help="Run or scaffold verify suite cases.",
    invoke_without_command=True,
    no_args_is_help=True,
)


class ShellChoice(str, Enum):
    zsh = "zsh"
    bash = "bash"


def register_meta(app: typer.Typer) -> None:
    app.command("usage")(_usage_cmd)
    app.command("credits")(_credits_cmd)
    app.command("update")(_update_cmd)
    app.add_typer(completions_app, name="completions")
    app.add_typer(verify_app, name="verify")


def _usage_cmd(
    include_test: bool = typer.Option(False, "--include-test", help="Include PARALLAX_TEST_MODE records (excluded by default)."),
) -> int:
    from .. import usage
    _print_usage(usage.summarize(include_test=include_test))
    return 0


def _credits_cmd() -> int:
    from ..openrouter import InsufficientCreditsError, check_credits
    try:
        balance = check_credits(min_balance_usd=0.0)
        print(
            f"OpenRouter credits — total ${balance.total:.2f}, "
            f"used ${balance.used:.2f}, remaining ${balance.remaining:.2f}"
        )
        if balance.remaining < 0.50:
            print("  ⚠ Low. Top up at https://openrouter.ai/settings/credits", file=sys.stderr)
            return 1
        return 0
    except InsufficientCreditsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: could not fetch credits ({type(e).__name__}: {e})", file=sys.stderr)
        return 1


def _update_cmd() -> int:
    return _run_update()


@completions_app.command("install")
def completions_install(
    shell: Optional[str] = typer.Option(None, "--shell", help="Target shell (default: detect from $SHELL)."),
    path: Optional[str] = typer.Option(None, "--path", help="Output path (default: ~/.cache/<shell>/parallax-completion.<shell>)."),
) -> int:
    if shell is not None and shell not in ("zsh", "bash"):
        typer.echo(f"Error: invalid shell '{shell}'. Choose from: zsh, bash", err=True)
        return 2
    return _run_completions_install(shell, path)


@completions_app.command("print")
def completions_print(
    shell: ShellChoice = typer.Argument(..., help="Shell to print completion for."),
) -> int:
    return _run_completions_print(shell.value)


@verify_app.command("suite")
def verify_suite(
    suite_dir: str = typer.Argument(..., help="Directory containing one or more case subfolders."),
    paid: bool = typer.Option(False, "--paid", help="Run cases marked paid: true (default skips them)."),
    case: Optional[str] = typer.Option(None, "--case", help="Run only a single case subfolder by name (default: all)."),
) -> int:
    from ..verify_suite import cli_run
    return cli_run(suite_dir, paid=paid, case=case)


@verify_app.command("init")
def verify_init(
    target: str = typer.Argument(..., help="Path to the new case folder."),
    from_dir: Optional[str] = typer.Option(None, "--from", help="Copy from an existing case folder."),
    resolution: Optional[str] = typer.Option(None, "--resolution", help="WxH (e.g. 480x854). Rewrites plan.yaml's resolution."),
    force: bool = typer.Option(False, "--force", help="Overwrite the target if it already exists (default: refuse)."),
) -> int:
    from ..verify_suite import cli_init
    return cli_init(
        target,
        from_dir=from_dir,
        resolution=resolution,
        force=force,
    )


def _print_usage(summary: dict) -> None:
    scope = "all records (incl. test mode)" if summary["include_test_mode"] else "real runs only"
    print(f"Usage summary — {scope}")
    print(f"Log: {summary['log_path']}")
    print(
        f"Totals: {summary['total_calls']} calls, "
        f"${summary['total_cost_usd']:.4f}, "
        f"{summary['total_duration_ms']} ms, "
        f"{summary['session_count']} sessions"
    )
    by_alias = summary["by_alias"]
    if not by_alias:
        print("(no records)")
        return
    print(f"{'alias':<14}{'tier':<10}{'calls':>7}{'cost_usd':>12}{'duration_ms':>14}")
    for alias, slot in sorted(by_alias.items()):
        print(
            f"{alias:<14}{slot['tier']:<10}{slot['calls']:>7}"
            f"{slot['cost_usd']:>12.4f}{slot['duration_ms']:>14}"
        )


def _run_update() -> int:
    import pathlib
    import re
    import urllib.request
    from importlib.metadata import version as pkg_version

    uv = shutil.which("uv")
    if not uv:
        print(
            "uv not found on PATH. Install it first:\n"
            "  curl -LsSf https://astral.sh/uv/install.sh | sh",
            file=sys.stderr,
        )
        return 1

    installed = pkg_version("parallax")

    remote: str | None = None
    try:
        url = "https://raw.githubusercontent.com/ianjamesburke/parallax-v0/main/pyproject.toml"
        with urllib.request.urlopen(url, timeout=5) as resp:
            content = resp.read().decode()
        m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        if m:
            remote = m.group(1)
    except Exception:
        print("Could not check remote version, upgrading anyway…", file=sys.stderr)

    if remote is not None and installed == remote:
        print(f"Already up to date (v{installed}).")
        return 0

    if remote is not None:
        print(f"Updating parallax v{installed} → v{remote}…")
    else:
        print("Upgrading parallax via uv tool upgrade --reinstall…")

    result = subprocess.run([uv, "tool", "upgrade", "parallax", "--reinstall"])
    if result.returncode != 0:
        return result.returncode

    _update_skill()
    return 0


def _update_skill() -> None:
    import pathlib
    skill_dir = pathlib.Path.home() / ".claude" / "skills" / "parallax"
    if not skill_dir.is_dir():
        return
    git = shutil.which("git")
    if not git:
        return
    print("Updating parallax skill…")
    result = subprocess.run(
        [git, "-C", str(skill_dir), "pull", "--ff-only"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        msg = result.stdout.strip() or result.stderr.strip()
        print(f"  Skill: {msg}")
    else:
        print(f"  Skill update failed: {result.stderr.strip()}", file=sys.stderr)


def _detect_shell() -> str:
    import os
    name = os.path.basename(os.environ.get("SHELL", ""))
    if name in {"zsh", "bash"}:
        return name
    return "zsh"


def _run_completions_print(shell: str) -> int:
    prog = "parallax"
    if shell == "zsh":
        print(f'eval "$(_PARALLAX_COMPLETE=zsh_source {prog})"')
    elif shell == "bash":
        print(f'eval "$(_PARALLAX_COMPLETE=bash_source {prog})"')
    return 0


def _run_completions_install(shell: str | None, path: str | None) -> int:
    from pathlib import Path

    target_shell = shell or _detect_shell()
    if target_shell not in {"zsh", "bash"}:
        print(f"Unsupported shell: {target_shell}", file=sys.stderr)
        return 1

    if path:
        out = Path(path).expanduser()
    else:
        out = Path.home() / ".cache" / target_shell / f"parallax-completion.{target_shell}"

    out.parent.mkdir(parents=True, exist_ok=True)
    prog = "parallax"
    var = f"_{prog.upper()}_COMPLETE"
    content = f'eval "$({var}={target_shell}_source {prog})"'
    out.write_text(content + "\n")
    print(f"Wrote {target_shell} completion to {out}")
    print(f"\nAdd to your ~/.{target_shell}rc:\n  source {out}")
    print(f"\nOr source completion directly:\n  eval \"$({var}={target_shell}_source {prog})\"")
    return 0
