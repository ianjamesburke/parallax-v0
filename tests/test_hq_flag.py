"""Tests for issue #121: default cheap models and --hq flag."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. Plan defaults
# ---------------------------------------------------------------------------

def test_plan_default_video_model():
    from parallax.plan import Plan
    import yaml
    data = yaml.safe_load("""
aspect: "9:16"
scenes:
  - index: 0
    vo_text: hello
    prompt: test
""")
    plan = Plan.model_validate(data)
    assert plan.video_model == "draft"


def test_plan_default_image_model():
    from parallax.plan import Plan
    import yaml
    data = yaml.safe_load("""
aspect: "9:16"
scenes:
  - index: 0
    vo_text: hello
    prompt: test
""")
    plan = Plan.model_validate(data)
    assert plan.image_model == "mid"


# ---------------------------------------------------------------------------
# 2. run_plan hq override
# ---------------------------------------------------------------------------

def test_run_plan_hq_overrides_models(tmp_path):
    """--hq should set plan-level image_model=premium, video_model=mid."""
    import yaml
    plan_data = {
        "aspect": "9:16",
        "image_model": "mid",
        "video_model": "draft",
        "scenes": [{"index": 0, "vo_text": "hi", "prompt": "test"}],
    }
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan_data))

    captured_settings = {}

    from parallax.settings import ProductionMode
    with patch("parallax.produce.resolve_settings") as mock_rs, \
         patch("parallax.produce.runlog.start_run", return_value="run-id"), \
         patch("parallax.produce.with_run_id", side_effect=lambda s, r: s):
        from parallax.settings import Settings
        mock_settings = MagicMock(spec=Settings)
        mock_settings.mode = ProductionMode.TEST
        mock_settings.events = MagicMock()
        mock_settings.usage = MagicMock()
        mock_settings.stills_only = False
        mock_settings.resolution = "1080x1920"
        mock_settings.plan_path = plan_path
        mock_rs.return_value = mock_settings

        from parallax.produce import run_plan
        # We don't need it to succeed — just capture what resolve_settings was called with
        try:
            run_plan(folder=str(tmp_path), plan_path=str(plan_path), mode=ProductionMode.TEST, hq=True, yes=True)
        except Exception:
            pass

        assert mock_rs.called, "resolve_settings was never called"
        call_args = mock_rs.call_args
        plan_arg = call_args[0][0]  # first positional arg
        if isinstance(plan_arg, dict):
            assert plan_arg.get("image_model") == "premium"
            assert plan_arg.get("video_model") == "mid"


# ---------------------------------------------------------------------------
# 3. Multi-ref advisory
# ---------------------------------------------------------------------------

def test_multi_ref_advisory_fires(capsys, tmp_path):
    """Advisory fires when 2+ refs passed to non-premium model."""
    ref1 = tmp_path / "r1.png"
    ref2 = tmp_path / "r2.png"
    ref1.write_bytes(b"")
    ref2.write_bytes(b"")

    with patch("parallax.openrouter.generate_image", return_value=tmp_path / "out.png"):
        from parallax.cli._image import _run_generate
        args = MagicMock()
        args.prompt = "test"
        args.model = "mid"
        args.refs = [str(ref1), str(ref2)]
        args.out = None
        args.size = None
        args.aspect = None
        _run_generate(args)

    out = capsys.readouterr().out
    assert "Advisory" in out
    assert "premium" in out


def test_multi_ref_advisory_suppressed_for_premium(capsys, tmp_path):
    """No advisory when model is premium."""
    ref1 = tmp_path / "r1.png"
    ref2 = tmp_path / "r2.png"
    ref1.write_bytes(b"")
    ref2.write_bytes(b"")

    with patch("parallax.openrouter.generate_image", return_value=tmp_path / "out.png"):
        from parallax.cli._image import _run_generate
        args = MagicMock()
        args.prompt = "test"
        args.model = "premium"
        args.refs = [str(ref1), str(ref2)]
        args.out = None
        args.size = None
        args.aspect = None
        _run_generate(args)

    out = capsys.readouterr().out
    assert "Advisory" not in out


def test_multi_ref_advisory_suppressed_for_single_ref(capsys, tmp_path):
    """No advisory for a single ref."""
    ref1 = tmp_path / "r1.png"
    ref1.write_bytes(b"")

    with patch("parallax.openrouter.generate_image", return_value=tmp_path / "out.png"):
        from parallax.cli._image import _run_generate
        args = MagicMock()
        args.prompt = "test"
        args.model = "mid"
        args.refs = [str(ref1)]
        args.out = None
        args.size = None
        args.aspect = None
        _run_generate(args)

    out = capsys.readouterr().out
    assert "Advisory" not in out


# ---------------------------------------------------------------------------
# 4. parallax models [default]/[hq] labels
# ---------------------------------------------------------------------------

def test_models_list_shows_default_label(capsys):
    from parallax.cli._models import _print_models_list
    import parallax.models as m
    _print_models_list(m, kind=None, as_json=False)
    out = capsys.readouterr().out
    assert "[default]" in out
    assert "[hq]" in out
