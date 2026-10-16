"""Spend gate: no provider call without a recorded human budget. Never touches the network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from parallax import cli
from parallax.produce import run_plan
from parallax.spend_guard import APPROVAL_FILE


@pytest.fixture(autouse=True)
def real_mode_no_network(monkeypatch, tmp_path):
    """Real mode with every provider key unset; any outbound HTTP call or provider function fails the test."""
    for key in ("PARALLAX_TEST_MODE", "OPENROUTER_API_KEY", "ELEVENLABS_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    calls: list[str] = []

    def record(module, name, forbid=False):
        real = getattr(module, name)

        def wrapper(*a, **k):
            calls.append(name)
            if forbid:
                raise AssertionError(f"network call attempted: {name}")
            return real(*a, **k)
        monkeypatch.setattr(module, name, wrapper)

    import httpx
    import parallax.openrouter as orr
    import parallax.voiceover as vo
    for name in ("post", "get"):
        record(httpx, name, forbid=True)
    for name in ("generate_image", "generate_video", "generate_tts", "check_credits"):
        record(orr, name)
    record(vo, "generate_voiceover_dict")
    return calls


def _plan(tmp_path: Path) -> Path:
    path = tmp_path / "plan.yaml"
    path.write_text(yaml.safe_dump({"resolution": "180x320", "captions": "skip", "scenes": [
        {"index": 0, "vo_text": "hello there", "prompt": "a red apple"}]}))
    return path


def _approve(folder: Path, budget: str) -> None:
    from parallax.spend_guard import approve
    approve(folder, float(budget), "tester")


def _refusal(capsys) -> dict:
    err = capsys.readouterr().err
    return json.loads(next(line for line in err.splitlines() if line.startswith("{")))


def test_produce_without_approval_is_refused_with_zero_provider_calls(tmp_path, real_mode_no_network, capsys):
    rc = cli.main(["produce", "--folder", str(tmp_path), "--plan", str(_plan(tmp_path))])
    payload = _refusal(capsys)
    assert rc == 1 and real_mode_no_network == []
    assert payload["gate"] == "spend-approval" and payload["estimate_usd"] > 0
    assert "parallax approve spend --folder" in payload["approve_command"]
    assert not (tmp_path / APPROVAL_FILE).exists()


def test_yes_flag_grants_nothing(tmp_path, real_mode_no_network):
    rc = cli.main(["produce", "--folder", str(tmp_path), "--plan", str(_plan(tmp_path)), "--yes"])
    assert rc == 2 and real_mode_no_network == []


def test_non_tty_alone_grants_nothing(tmp_path, real_mode_no_network, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: False, raising=False)
    assert cli.main(["produce", "--folder", str(tmp_path), "--plan", str(_plan(tmp_path))]) == 1
    assert real_mode_no_network == [] and _refusal(capsys)["gate"] == "spend-approval"


def test_budget_exceeded_is_refused(tmp_path, real_mode_no_network, capsys):
    _approve(tmp_path, "0.0001")
    rc = cli.main(["produce", "--folder", str(tmp_path), "--plan", str(_plan(tmp_path))])
    payload = _refusal(capsys)
    assert rc == 1 and real_mode_no_network == []
    assert payload["budget_usd"] == 0.0001 and payload["reserved_usd"] == 0.0


def test_approval_reserves_estimate_then_budget_runs_out(tmp_path, monkeypatch):
    from parallax.spend_guard import SpendRefused, require_approval
    _approve(tmp_path, "1.00")
    require_approval(tmp_path, 0.60, "x")
    with pytest.raises(SpendRefused, match="left"):
        require_approval(tmp_path, 0.60, "x")
    doc = yaml.safe_load((tmp_path / APPROVAL_FILE).read_text())
    assert [r["usd"] for r in doc["reserved"]] == [0.6]


def test_invalid_approval_throws_naming_file_and_fix(tmp_path):
    from parallax.spend_guard import require_approval
    (tmp_path / APPROVAL_FILE).write_text("budget_usd: lots\nauthorized_by: x\n")
    with pytest.raises(ValueError, match=r"spend-approval\.yaml.*budget_usd.*approve spend"):
        require_approval(tmp_path, 0.1, "x")


def test_approve_preserves_comments_and_ledger(tmp_path):
    from parallax.spend_guard import require_approval
    _approve(tmp_path, "1")
    require_approval(tmp_path, 0.25, "x")
    path = tmp_path / APPROVAL_FILE
    path.write_text("# my note\n" + path.read_text())
    _approve(tmp_path, "2")
    text = path.read_text()
    assert "# my note" in text and "budget_usd: 2" in text and "usd: 0.25" in text


def test_approved_produce_proceeds_in_test_mode(tmp_path, monkeypatch, real_mode_no_network):
    from parallax.settings import ProductionMode
    result = run_plan(tmp_path, _plan(tmp_path), mode=ProductionMode.TEST)
    assert result.error is None, result.error
    assert "post" not in real_mode_no_network and "get" not in real_mode_no_network
    assert not (tmp_path / APPROVAL_FILE).exists()  # test mode spends nothing, needs and records nothing


def test_approved_real_run_passes_the_gate(tmp_path, real_mode_no_network):
    """With a budget recorded the gate opens and reserves; with no key, the provider layer is what stops the run."""
    _approve(tmp_path, "5")
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        run_plan(tmp_path, _plan(tmp_path))
    assert "generate_image" in real_mode_no_network and "post" not in real_mode_no_network
    doc = yaml.safe_load((tmp_path / APPROVAL_FILE).read_text())
    assert doc["reserved"] and doc["reserved"][0]["usd"] > 0


def test_image_generate_without_approval_is_refused(tmp_path, real_mode_no_network, capsys):
    assert cli.main(["image", "generate", "an apple", "--out", str(tmp_path / "a.png")]) == 1
    assert real_mode_no_network == [] and _refusal(capsys)["gate"] == "spend-approval"
    assert cli.main(["image", "generate", "an apple", "--out", str(tmp_path / "a.png"), "--folder", str(tmp_path)]) == 1
    assert real_mode_no_network == [] and not (tmp_path / "a.png").exists()


def test_video_animate_and_voiceover_without_approval_are_refused(tmp_path, real_mode_no_network, capsys):
    assert cli.main(["video", "animate", "--prompt", "pan", "--folder", str(tmp_path)]) == 1
    assert cli.main(["audio", "voiceover", "--text", "hi", "--out", str(tmp_path / "v.mp3"), "--folder", str(tmp_path)]) == 1
    assert real_mode_no_network == []


def test_unpriced_tts_model_is_refused_not_guessed(tmp_path, real_mode_no_network, capsys):
    _approve(tmp_path, "5")
    assert cli.main(["audio", "voiceover", "--text", "hi", "--voice-model", "elevenlabs", "--out", str(tmp_path / "v.mp3"), "--folder", str(tmp_path)]) == 1
    assert "no catalog price" in capsys.readouterr().err and real_mode_no_network == []


def test_scene_preview_is_free_and_makes_no_provider_call(tmp_path, real_mode_no_network, monkeypatch):
    """produce --scene only runs ffmpeg on an existing still/clip; it has no provider path, so it needs no approval."""
    import parallax.produce as produce_mod
    seen = {}
    monkeypatch.setattr(produce_mod, "test_scene", lambda **kw: seen.update(kw) or 0)
    assert cli.main(["produce", "--folder", str(tmp_path), "--plan", str(_plan(tmp_path)), "--scene", "0"]) == 0
    assert seen["scene_index"] == 0 and real_mode_no_network == []


def test_test_mode_image_needs_no_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    monkeypatch.setenv("PARALLAX_OUTPUT_DIR", str(tmp_path / "out"))
    assert cli.main(["image", "generate", "an apple", "--out", str(tmp_path / "a.png")]) == 0


def test_guide_exposes_spend_approval():
    import typer
    from parallax.guide import build_guide
    guide = build_guide(typer.main.get_group(cli.app))
    stage = next(s for s in guide["stages"] if s["id"] == "spend_approval")
    assert stage["commands"][0]["command"] == "parallax approve spend" and "human_decision" in stage
    assert any("spend-approval.yaml" in rule for rule in guide["rules"])


def test_approve_command_refuses_without_a_tty_and_writes_nothing(tmp_path, capsys):
    rc = cli.main(["approve", "spend", "--folder", str(tmp_path), "--budget-usd", "987", "--authorized-by", "agentinvented"])
    err = capsys.readouterr().err
    assert rc == 2 and not (tmp_path / APPROVAL_FILE).exists()
    assert "interactive terminal" in err and "! parallax approve spend --folder" in err
    assert "--budget-usd <budget> --authorized-by <your name>" in err
    assert "987" not in err and "agentinvented" not in err


def _run_in_pty(tmp_path, typed: str | None) -> tuple[int, str]:
    import os
    import pty
    import subprocess
    import sys
    master, slave = pty.openpty()
    code = "import sys; from parallax import cli; sys.exit(cli.main(sys.argv[1:]))"
    proc = subprocess.Popen([sys.executable, "-c", code, "approve", "spend", "--folder", str(tmp_path),
                             "--budget-usd", "5", "--authorized-by", "ian"],
                            stdin=slave, stdout=slave, stderr=slave, close_fds=True)
    os.close(slave)
    if typed is not None:
        os.write(master, (typed + "\n").encode())
    out = b""
    while True:
        try:
            chunk = os.read(master, 4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    os.close(master)
    return proc.wait(timeout=30), out.decode()


def test_approve_command_in_a_tty_needs_the_typed_budget(tmp_path):
    rc, out = _run_in_pty(tmp_path, "7")
    assert rc == 2 and "does not match" in out and not (tmp_path / APPROVAL_FILE).exists()
    rc, out = _run_in_pty(tmp_path, "5")
    assert rc == 0 and yaml.safe_load((tmp_path / APPROVAL_FILE).read_text())["budget_usd"] == 5


def test_image_analyze_is_gated_even_in_test_mode(tmp_path, monkeypatch, real_mode_no_network, capsys):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    img = tmp_path / "i.png"
    img.write_bytes(b"x")
    assert cli.main(["image", "analyze", str(img), "--folder", str(tmp_path)]) == 1
    assert _refusal(capsys)["gate"] == "spend-approval" and real_mode_no_network == []



def test_guide_lists_every_paid_command_with_folder():
    import typer
    from parallax.guide import build_guide
    stage = next(s for s in build_guide(typer.main.get_group(cli.app))["stages"] if s["id"] == "spend_approval")
    paid = {c["command"]: c for c in stage["commands"]}
    for name in ("produce", "image generate", "image analyze", "video animate", "audio voiceover"):
        assert "--folder" in paid[f"parallax {name}"]["pass"], name
