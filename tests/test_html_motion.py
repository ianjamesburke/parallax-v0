"""Offline ($0) tests for HTML/SVG + GSAP motion-graphics styles, renderer, layout guard, brand wiring, CLI, and produce integration."""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from parallax import cli, guide_topics
from parallax.brief import Brief
from parallax.html_motion import (
    CANONICAL_STYLE_ORDER,
    DEFAULT_MOTION_MODEL,
    LayoutViolationError,
    audit_and_repair_html,
    audit_layout,
    available_style_names,
    find_chrome_binary,
    generate_scene_html,
    list_style_presets,
    load_style_preset,
    patch_html_timing,
    render_html_to_mp4,
    resolve_motion_model,
)
from parallax.motion_graphics import BRAND_DEFAULT, _colors, merge_brand_palette
from parallax.openrouter import client as openrouter_client
from parallax.plan import Plan
from parallax.produce import run_plan
from parallax.settings import ProductionMode


def _has_chrome() -> bool:
    try:
        find_chrome_binary()
        return True
    except RuntimeError:
        return False


requires_chrome = pytest.mark.skipif(
    not _has_chrome(),
    reason="Headless Chrome/Chromium binary not found; set PARALLAX_CHROME or install google-chrome.",
)


@pytest.fixture(autouse=True)
def _force_test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")


def _probe_video(mp4_path: Path) -> dict[str, Any]:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=width,height,nb_read_frames,r_frame_rate:format=duration",
            "-of",
            "json",
            str(mp4_path),
        ],
        text=True,
    )
    return json.loads(out)


# ---------------------------------------------------------------------------
# 1. Preset loading & CLI --list-styles
# ---------------------------------------------------------------------------


def test_preset_loading_all_five_styles_and_unknown_style_errors() -> None:
    presets = list_style_presets()
    names = [p["name"] for p in presets]
    assert names == list(CANONICAL_STYLE_ORDER)
    assert set(available_style_names()) == {
        "flat-explainer",
        "neo-brutal",
        "glass-ui",
        "editorial-clinical",
        "kinetic-type",
    }

    for style_name in CANONICAL_STYLE_ORDER:
        preset = load_style_preset(style_name)
        assert preset["name"] == style_name
        assert preset["description"]
        assert preset["design_rules"]
        assert set(preset["palette"].keys()) >= {"bg", "surface", "ink", "accent", "accent2"}
        assert set(preset["fonts"].keys()) >= {"heading", "body"}
        assert preset["motion_vocabulary"]
        assert preset["do"]
        assert preset["dont"]

    with pytest.raises(ValueError, match="Unknown motion style 'nonexistent-style'"):
        load_style_preset("nonexistent-style")


def test_cli_list_styles_text_and_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["video", "motion", "--list-styles"])
    assert rc == 0
    stdout = capsys.readouterr().out
    for style_name in CANONICAL_STYLE_ORDER:
        assert style_name in stdout

    rc_json = cli.main(["video", "motion", "--list-styles", "--json"])
    assert rc_json == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["name"] for item in payload] == list(CANONICAL_STYLE_ORDER)


def test_default_motion_model_and_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PARALLAX_MOTION_MODEL", raising=False)
    assert DEFAULT_MOTION_MODEL == "google/gemini-2.5-flash"
    assert resolve_motion_model() == "google/gemini-2.5-flash"
    monkeypatch.setenv("PARALLAX_MOTION_MODEL", "google/gemini-2.5-flash-lite")
    assert resolve_motion_model() == "google/gemini-2.5-flash-lite"
    assert resolve_motion_model("custom/model") == "custom/model"


# ---------------------------------------------------------------------------
# 2. Test mode ($0, zero network calls) & HTML contract
# ---------------------------------------------------------------------------


def test_generate_scene_html_test_mode_zero_network_and_inlines_gsap_brand_logo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _forbid_post(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"OpenRouter _post must not be called in test mode (got {args}, {kwargs})")

    monkeypatch.setattr(openrouter_client, "_post", _forbid_post)

    logo_svg = tmp_path / "brand_logo.svg"
    logo_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40"><rect width="120" height="40" fill="#FF3366"/></svg>',
        encoding="utf-8",
    )
    brand = {
        "name": "AcmeGuard",
        "logo": str(logo_svg),
        "palette": {
            "primary": "#FF3366",
            "secondary": "#00E5A3",
            "bg": "#101827",
            "ink": "#F9FAFB",
        },
        "fonts": {"heading": "'Space Grotesk', sans-serif", "body": "'Inter', sans-serif"},
    }
    words = [
        {"word": "Stop", "start": 0.1, "end": 0.45},
        {"word": "scam!", "start": 0.85, "end": 1.35},
    ]

    for style_name in CANONICAL_STYLE_ORDER:
        html = generate_scene_html(
            scene_text="Stop scam calls before they ring.",
            style=style_name,
            brand=brand,
            duration_s=3.5,
            words=words,
        )
        assert "gsap.timeline" in html
        assert "paused: true" in html or "paused:true" in html
        assert "window.__seek" in html
        assert "window.__TIMING" in html
        assert "#FF3366" in html
        assert 'id="brand-logo"' in html
        assert "data:image/svg+xml;base64," in html
        assert '<script src="http' not in html.lower()


# ---------------------------------------------------------------------------
# 3. Render & 4. Retime (`--from-html`)
# ---------------------------------------------------------------------------


@requires_chrome
def test_render_html_to_mp4_exact_dimensions_and_frame_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        openrouter_client,
        "_post",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("_post must not be called")),
    )
    html = generate_scene_html(
        scene_text="Instant call protection in one tap.",
        style="flat-explainer",
        brand={"name": "Shield", "palette": {"primary": "#0059FF"}},
        duration_s=1.0,
        words=[{"word": "Instant", "start": 0.1, "end": 0.4}],
    )
    html_path = tmp_path / "scene.html"
    mp4_path = tmp_path / "scene.mp4"
    html_path.write_text(html, encoding="utf-8")

    fps = 8
    duration_s = 1.0
    out = render_html_to_mp4(html_path, mp4_path, duration_s=duration_s, fps=fps)
    assert out == mp4_path and mp4_path.is_file()

    probe = _probe_video(mp4_path)
    stream = probe["streams"][0]
    assert int(stream["width"]) == 1080
    assert int(stream["height"]) == 1920
    assert int(stream["nb_read_frames"]) == round(duration_s * fps)


@requires_chrome
def test_retime_from_html_cli_zero_llm_calls_and_updates_duration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        openrouter_client,
        "_post",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("_post must not be called")),
    )

    initial_words = tmp_path / "words_v1.json"
    initial_words.write_text(
        json.dumps([{"word": "Block", "start": 0.1, "end": 0.4}, {"word": "scams", "start": 0.5, "end": 0.9}]),
        encoding="utf-8",
    )
    out_mp4_v1 = tmp_path / "scene_v1.mp4"
    rc1 = cli.main(
        [
            "video",
            "motion",
            "--style",
            "neo-brutal",
            "--text",
            "Block scams immediately.",
            "--words",
            str(initial_words),
            "--duration",
            "1.0",
            "--fps",
            "6",
            "--out",
            str(out_mp4_v1),
        ]
    )
    assert rc1 == 0
    html_v1 = out_mp4_v1.with_suffix(".html")
    assert html_v1.is_file()
    assert out_mp4_v1.is_file()
    capsys.readouterr()

    # Now re-time via --from-html with new words and a new duration (1.5s @ 6fps = 9 frames)
    new_words = tmp_path / "words_v2.json"
    new_words.write_text(
        json.dumps(
            {
                "words": [
                    {"word": "Block", "start": 0.2, "end": 0.6},
                    {"word": "phishing", "start": 0.8, "end": 1.3},
                ],
                "total_duration_s": 1.5,
            }
        ),
        encoding="utf-8",
    )
    out_mp4_v2 = tmp_path / "scene_v2.mp4"
    html_v2 = tmp_path / "scene_v2.html"
    rc2 = cli.main(
        [
            "video",
            "motion",
            "--from-html",
            str(html_v1),
            "--words",
            str(new_words),
            "--duration",
            "1.5",
            "--fps",
            "6",
            "--out",
            str(out_mp4_v2),
            "--html-out",
            str(html_v2),
        ]
    )
    assert rc2 == 0
    patched_text = html_v2.read_text(encoding="utf-8")
    assert '"phishing"' in patched_text
    assert "duration: 1.5" in patched_text

    probe = _probe_video(out_mp4_v2)
    stream = probe["streams"][0]
    assert int(stream["width"]) == 1080
    assert int(stream["height"]) == 1920
    assert int(stream["nb_read_frames"]) == round(1.5 * 6)


# ---------------------------------------------------------------------------
# 5. Layout guard (bad HTML flagged, good HTML passes, real-mode repair pass)
# ---------------------------------------------------------------------------


@requires_chrome
def test_layout_guard_flags_bad_html_and_passes_good_html() -> None:
    good_html = generate_scene_html(
        scene_text="Clinical-grade sleep tracking for every night.",
        style="editorial-clinical",
        duration_s=2.0,
    )
    good_audit = audit_layout(good_html, duration_s=2.0, sample_fractions=(0.25, 0.75))
    assert good_audit.passed
    assert good_audit.violations == []

    bad_html = """<!DOCTYPE html>
<html>
<body style="margin:0;width:1080px;height:1920px;position:relative;overflow:hidden;font-family:sans-serif;">
  <div style="position:absolute;left:15px;top:30px;font-size:36px;">Outside safe box top-left</div>
  <div style="position:absolute;left:180px;top:320px;font-size:48px;">Overlapping Line One</div>
  <div style="position:absolute;left:185px;top:325px;font-size:48px;">Overlapping Line Two</div>
  <div style="position:absolute;left:150px;top:1520px;font-size:44px;">Intruding into caption zone</div>
  <div style="position:absolute;left:150px;top:620px;width:70px;overflow:hidden;white-space:nowrap;font-size:36px;">ClippedOverflowingTextString</div>
</body>
</html>"""
    bad_audit = audit_layout(bad_html, duration_s=1.0, sample_fractions=(0.5,))
    assert not bad_audit.passed
    violation_types = {v["type"] for v in bad_audit.violations}
    assert "outside_safe_box" in violation_types
    assert "text_overlap" in violation_types
    assert "caption_zone_intrusion" in violation_types
    assert "text_clipped" in violation_types


@requires_chrome
def test_layout_guard_real_mode_one_repair_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARALLAX_TEST_MODE", "0")
    good_html = generate_scene_html.__wrapped__ if hasattr(generate_scene_html, "__wrapped__") else None
    # Build a known-good HTML in test mode first for the mocked repair response
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    valid_html = generate_scene_html("Safe layout after repair", "glass-ui", duration_s=1.0)
    monkeypatch.setenv("PARALLAX_TEST_MODE", "0")

    bad_html = """<!DOCTYPE html><html><body style="margin:0;width:1080px;height:1920px;position:relative;">
      <div style="position:absolute;left:10px;top:1700px;font-size:42px;">Cropped and in caption zone</div>
    </body></html>"""

    calls: list[dict[str, Any]] = []

    def _mock_post(path: str, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append({"path": path, "body": body})
        return {
            "id": "gen-mock-repair-1",
            "choices": [{"message": {"content": valid_html}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 240, "total_tokens": 360, "cost": 0.0002},
        }

    monkeypatch.setattr(openrouter_client, "_post", _mock_post)
    repaired_html, result = audit_and_repair_html(
        bad_html,
        duration_s=1.0,
        style="glass-ui",
    )
    assert len(calls) == 1
    assert calls[0]["path"] == "/chat/completions"
    assert result.passed
    assert result.repaired
    assert "gsap.timeline" in repaired_html


# ---------------------------------------------------------------------------
# 6. Brand wiring (PIL templates merge over BRAND_DEFAULT + produce reads plan.brand)
# ---------------------------------------------------------------------------


def test_brand_palette_merges_over_brand_default_for_pil_templates() -> None:
    custom_brand = {
        "name": "Malwarebytes",
        "palette": {
            "primary": "#0059FF",
            "secondary": "#00C853",
            "bg": "#0A1628",
            "surface": "#132340",
            "ink": "#F8FAFC",
        },
    }
    merged = merge_brand_palette(custom_brand)
    assert merged["bg"] == "#0A1628"
    assert merged["bg2"] == "#132340"
    assert merged["accent"] == "#0059FF"
    assert merged["good"] == "#00C853"
    assert merged["text"] == "#F8FAFC"
    assert merged["muted"] == BRAND_DEFAULT["muted"]
    assert merged["sunrise"][0] == "#0059FF"

    colors_via_helper = _colors(None, brand=custom_brand)
    assert colors_via_helper["accent"] == "#0059FF"
    assert colors_via_helper["bg"] == "#0A1628"


# ---------------------------------------------------------------------------
# 7. Produce integration (motion_graphic + motion_style writes html_path + clip_path)
# ---------------------------------------------------------------------------


@requires_chrome
def test_produce_integration_motion_graphic_with_motion_style_writes_clip_and_html(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        openrouter_client,
        "_post",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("_post must not be called in test mode")),
    )

    logo_file = tmp_path / "logo.svg"
    logo_file.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><circle cx="40" cy="40" r="36" fill="#E11D48"/></svg>',
        encoding="utf-8",
    )

    brief_data = {
        "goal": "Promote scam call protection",
        "aspect": "9:16",
        "voice": "nova",
        "brand": {
            "name": "PulseShield",
            "logo": "logo.svg",
            "palette": {"primary": "#E11D48", "bg": "#0F172A", "ink": "#FFFFFF"},
        },
        "script": {
            "scenes": [
                {
                    "index": 0,
                    "shot_type": "screen",
                    "scene_type": "motion_graphic",
                    "motion_style": "kinetic-type",
                    "vo_text": "Stop unknown scam callers instantly.",
                    "prompt": "Kinetic typography warning about scam callers.",
                }
            ]
        },
    }
    brief_path = tmp_path / "brief.yaml"
    brief_path.write_text(yaml.safe_dump(brief_data, sort_keys=False), encoding="utf-8")

    brief = Brief.from_yaml(brief_path)
    plan_skeleton = brief.to_plan_skeleton()
    assert plan_skeleton["scenes"][0]["motion_style"] == "kinetic-type"
    assert plan_skeleton["brand"]["name"] == "PulseShield"

    plan_dir = tmp_path / "parallax" / "scratch"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / "plan.yaml"
    plan_skeleton["captions"] = "skip"
    plan_path.write_text(yaml.safe_dump(plan_skeleton, sort_keys=False), encoding="utf-8")

    # Validate Plan schema accepts motion_graphic with motion_style and no clip_path
    loaded_plan = Plan.from_yaml(plan_path)
    assert loaded_plan.scenes[0].motion_style == "kinetic-type"
    assert loaded_plan.scenes[0].clip_path is None

    res = run_plan(folder=tmp_path, plan_path=plan_path, mode=ProductionMode.TEST)
    assert res.status == "ok", f"produce failed: {res.error}"
    assert res.final_video is not None and res.final_video.is_file()

    updated_plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    scene0 = updated_plan["scenes"][0]
    assert scene0.get("html_path"), f"Expected html_path in plan.yaml scene 0, got {scene0}"
    assert scene0.get("clip_path"), f"Expected clip_path in plan.yaml scene 0, got {scene0}"

    resolved_html = tmp_path / scene0["html_path"]
    resolved_clip = tmp_path / scene0["clip_path"]
    assert resolved_html.is_file()
    assert resolved_clip.is_file()

    html_content = resolved_html.read_text(encoding="utf-8")
    assert "#E11D48" in html_content
    assert "PulseShield" in html_content
    assert "data:image/svg+xml;base64," in html_content


# ---------------------------------------------------------------------------
# 8. Guide cleanup (single motion_graphic key, no Remotion mention)
# ---------------------------------------------------------------------------


def test_guide_topics_has_single_motion_graphic_key_and_no_remotion() -> None:
    guide_file = Path(guide_topics.__file__).resolve()
    source = guide_file.read_text(encoding="utf-8")
    tree = ast.parse(source)

    topics_dict_node: ast.Dict | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "TOPICS":
            if isinstance(node.value, ast.Dict):
                topics_dict_node = node.value
                break
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "TOPICS" and isinstance(node.value, ast.Dict):
                    topics_dict_node = node.value
                    break

    assert topics_dict_node is not None, "Could not locate TOPICS dict in guide_topics.py"
    keys = [
        k.value for k in topics_dict_node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
    ]
    assert keys.count("motion_graphic") == 1, f"Expected 'motion_graphic' key once, found {keys.count('motion_graphic')}"

    mg_topic = guide_topics.TOPICS["motion_graphic"]
    serialized = json.dumps(mg_topic)
    assert "Remotion" not in serialized
    assert "remotion" not in serialized
    assert "parallax video motion --style" in serialized
    for style_name in CANONICAL_STYLE_ORDER:
        assert style_name in serialized
