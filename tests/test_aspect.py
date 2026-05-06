"""Phase 1.3 — aspect ratio first-class.

Verifies the user-facing `aspect:` knob flows from plan/CLI through
`Settings` and out to the image/video calls. Three layers:

  1. `resolve_settings` derives the right resolution per aspect, validates
     the value, and respects an explicit `resolution:` override.
  2. The image generation prompt cue and request body reflect the chosen
     aspect (no leaking 9:16 when the caller picks something else).
  3. End-to-end `parallax produce --aspect <N>` (test mode, stub provider)
     produces an mp4 whose dimensions match the chosen aspect.
"""

from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path

import httpx
import pytest
import yaml

from parallax import openrouter, runlog
from parallax.settings import _ASPECT_TO_RESOLUTION, resolve_settings


# ---------------------------------------------------------------------------
# Settings layer
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("aspect,expected", list(_ASPECT_TO_RESOLUTION.items()))
def test_resolve_settings_derives_resolution_from_aspect(tmp_path, aspect, expected):
    plan = {"aspect": aspect, "scenes": [{"index": 0, "vo_text": "x", "prompt": "p"}]}
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan))
    settings = resolve_settings(plan, tmp_path, plan_path)
    assert settings.aspect == aspect
    assert settings.resolution == expected


def test_resolve_settings_explicit_resolution_overrides_aspect_derivation(tmp_path):
    plan = {
        "aspect": "16:9",
        "resolution": "640x480",  # not the natural pair for 16:9
        "scenes": [{"index": 0, "vo_text": "x", "prompt": "p"}],
    }
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan))
    settings = resolve_settings(plan, tmp_path, plan_path)
    assert settings.aspect == "16:9"
    assert settings.resolution == "640x480"


def test_resolve_settings_rejects_invalid_aspect(tmp_path):
    plan = {"aspect": "7:5", "scenes": [{"index": 0, "vo_text": "x", "prompt": "p"}]}
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan))
    with pytest.raises(ValueError, match="7:5"):
        resolve_settings(plan, tmp_path, plan_path)


def test_resolve_settings_defaults_to_9_16_when_aspect_absent(tmp_path):
    plan = {"scenes": [{"index": 0, "vo_text": "x", "prompt": "p"}]}
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan))
    settings = resolve_settings(plan, tmp_path, plan_path)
    assert settings.aspect == "9:16"
    assert settings.resolution == "720x1280"


# ---------------------------------------------------------------------------
# Image-call layer — aspect_ratio flows into the request body, not from spec
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, content=b""):
        self.status_code = status_code
        self._json = json_body or {}
        self.content = content
        self.text = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)  # type: ignore[arg-type]

    def json(self):
        return self._json


@pytest.fixture
def real_mode_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("PARALLAX_TEST_MODE", raising=False)
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))
    runlog.start_run("aspect-test")
    yield
    runlog.end_run()


@pytest.mark.parametrize("aspect", list(_ASPECT_TO_RESOLUTION.keys()))
def test_generate_image_forwards_aspect_ratio_to_request_body(monkeypatch, tmp_path, real_mode_env, aspect):
    posts: list[dict] = []
    b64 = base64.b64encode(b"\x89PNG").decode()

    def fake_post(url, *, headers, json, timeout):
        posts.append(json)
        return _FakeResponse(200, {
            "choices": [{"message": {"images": [{"image_url": {"url": f"data:image/png;base64,{b64}"}}]}}],
        })

    monkeypatch.setattr(httpx, "post", fake_post)

    openrouter.generate_image("a thing", alias="draft", out_dir=tmp_path, aspect_ratio=aspect)

    body = posts[0]
    assert body["aspect_ratio"] == aspect
    # The textual cue is prepended to the prompt — locks in the per-aspect string.
    text_part = body["messages"][0]["content"][0]["text"]
    assert aspect in text_part, f"prompt cue missing {aspect!r}: {text_part[:80]!r}"


def test_generate_image_omits_aspect_when_unset(monkeypatch, tmp_path, real_mode_env):
    posts: list[dict] = []
    b64 = base64.b64encode(b"\x89PNG").decode()

    def fake_post(url, *, headers, json, timeout):
        posts.append(json)
        return _FakeResponse(200, {
            "choices": [{"message": {"images": [{"image_url": {"url": f"data:image/png;base64,{b64}"}}]}}],
        })

    monkeypatch.setattr(httpx, "post", fake_post)

    openrouter.generate_image("a thing", alias="draft", out_dir=tmp_path)
    assert "aspect_ratio" not in posts[0]


# ---------------------------------------------------------------------------
# End-to-end — produce a stub-mode video at every supported aspect
# ---------------------------------------------------------------------------

def _ffprobe_dimensions(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    w, h = out.split(",")
    return int(w), int(h)


@pytest.mark.parametrize("aspect", list(_ASPECT_TO_RESOLUTION.keys()))
def test_produce_in_test_mode_outputs_correct_dimensions(monkeypatch, tmp_path, aspect):
    """Drives `parallax.produce.run_plan` (test mode → shim) and verifies
    the final mp4's dimensions match the chosen aspect."""
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))

    folder = tmp_path / "0099_aspect_smoke"
    folder.mkdir()
    plan = {
        "voice": "nova",
        "image_model": "mid",
        "video_model": "mid",
        "captions": "skip",
        # aspect intentionally NOT set on plan — pass via override arg below.
        "scenes": [
            {"index": 0, "shot_type": "broll",
             "vo_text": "One short line.", "prompt": "A still scene."},
        ],
    }
    plan_path = folder / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan))

    from parallax.produce import run_plan
    result = run_plan(folder=folder, plan_path=plan_path, aspect=aspect)
    assert result.status == "ok", f"unexpected error: {result.error}"

    # Locate the produced mp4 — convention is `<folder.name>-vN.mp4`.
    candidates = list(folder.rglob(f"{folder.name}-v*.mp4"))
    assert candidates, f"no produced mp4 found under {folder}"
    final_mp4 = candidates[0]

    expected_res = _ASPECT_TO_RESOLUTION[aspect]
    exp_w, exp_h = (int(x) for x in expected_res.split("x"))
    w, h = _ffprobe_dimensions(final_mp4)
    assert (w, h) == (exp_w, exp_h), (
        f"aspect {aspect}: produced mp4 is {w}x{h}, expected {exp_w}x{exp_h}"
    )
