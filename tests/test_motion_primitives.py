"""Offline ($0) tests for the SceneSpec motion primitives layer (`src/parallax/motion_primitives.py`)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from parallax import cli
from parallax.html_motion import (
    CANONICAL_STYLE_ORDER,
    _CDPPipeSession,
    audit_layout,
    find_chrome_binary,
    generate_scene_html,
    generate_scene_spec_and_html,
    load_brand_file,
    load_style_preset,
    normalize_brand,
    wcag_contrast_ratio,
)
from parallax.motion_primitives import (
    CHAR_LIMITS,
    CHILD_CAPS,
    STAGE_BOTTOM,
    STAGE_CENTER_X,
    STAGE_LEFT,
    STAGE_RIGHT,
    STAGE_TOP,
    build_default_scene_spec,
    render_scene_spec_html,
    resolve_brand_header_contrast,
    validate_scene_spec,
)
from parallax.openrouter import client as openrouter_client


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


def _sample_malwarebytes_brand() -> dict[str, Any]:
    sample_path = Path("/workspace/francisco-recordings/motion-styles-v1/inputs/brand.yaml")
    if sample_path.is_file():
        return load_brand_file(sample_path)
    return {
        "name": "Malwarebytes (placeholder)",
        "palette": {
            "primary": "#0066FF",
            "secondary": "#00D1B2",
            "bg": "#FFFFFF",
            "ink": "#001A4D",
            "accent": "#FF5C39",
        },
        "fonts": {
            "heading": "Inter",
            "body": "Inter",
        },
    }


def _evaluate_in_chrome(html: str, tmp_path: Path, js_expr: str, name: str = "scene.html") -> Any:
    chrome_bin = find_chrome_binary()
    html_path = tmp_path / name
    html_path.write_text(html, encoding="utf-8")
    with _CDPPipeSession(chrome_bin) as cdp:
        cdp.open_page(html_path)
        res = cdp.send(
            "Runtime.evaluate",
            {"expression": js_expr, "returnByValue": True, "awaitPromise": True},
        )
        assert "exceptionDetails" not in res, f"JS evaluation threw: {res.get('exceptionDetails')}"
        return (res.get("result") or {}).get("value")


# ---------------------------------------------------------------------------
# 1. Centering (|center - 540| <= 4px) & safe bounds (bottom <= 1320, x in [70, 1010])
# ---------------------------------------------------------------------------


@requires_chrome
def test_all_five_styles_centering_and_safe_bounds_in_chrome(tmp_path: Path) -> None:
    brand = _sample_malwarebytes_brand()
    scene_text = (
        "Think of Malwarebytes Scam Guard like a personal bodyguard inside your phone. "
        "Before you answer a call or click a link, it checks if it is a scam."
    )
    words = [
        {"word": "Think", "start": 0.05, "end": 0.25},
        {"word": "Scam", "start": 0.90, "end": 1.20},
        {"word": "Guard", "start": 1.20, "end": 1.50},
        {"word": "answer", "start": 3.40, "end": 3.80},
    ]
    duration_s = 5.0

    inspect_js = f"""
    (() => {{
      if (typeof window.__seek === 'function') window.__seek({duration_s});
      const stage = document.getElementById('safe-stage');
      if (!stage) return {{ error: 'missing #safe-stage' }};
      const stageRect = stage.getBoundingClientRect();
      const children = Array.from(stage.children).map((el) => {{
        const r = el.getBoundingClientRect();
        return {{
          id: el.id,
          primitive: el.getAttribute('data-primitive') || el.tagName,
          left: r.left,
          right: r.right,
          top: r.top,
          bottom: r.bottom,
          width: r.width,
          centerX: (r.left + r.right) / 2.0,
        }};
      }});
      const keyNodes = Array.from(stage.querySelectorAll('.phone-frame, .feature-card, .cta-button, .brand-plate')).map((el) => {{
        const r = el.getBoundingClientRect();
        return {{
          cls: el.className,
          left: r.left,
          right: r.right,
          centerX: (r.left + r.right) / 2.0,
        }};
      }});
      const textBoxes = [];
      const all = Array.from(document.querySelectorAll('body *'));
      const ignored = new Set(['SCRIPT', 'STYLE', 'TITLE', 'HEAD', 'META', 'LINK', 'DEFS', 'DESC']);
      for (const el of all) {{
        if (ignored.has(el.tagName.toUpperCase())) continue;
        let hasText = false;
        for (let i = 0; i < el.childNodes.length; i++) {{
          const n = el.childNodes[i];
          if (n.nodeType === 3 && n.nodeValue && n.nodeValue.trim().length > 0) {{
            hasText = true;
            break;
          }}
        }}
        if (!hasText) continue;
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) <= 0.05) continue;
        const r = el.getBoundingClientRect();
        if (r.width <= 0.5 || r.height <= 0.5) continue;
        textBoxes.push({{
          text: (el.textContent || '').trim().slice(0, 40),
          left: r.left,
          right: r.right,
          top: r.top,
          bottom: r.bottom,
        }});
      }}
      return {{
        stageRect: {{
          left: stageRect.left,
          top: stageRect.top,
          right: stageRect.right,
          bottom: stageRect.bottom,
          width: stageRect.width,
          height: stageRect.height,
        }},
        children,
        keyNodes,
        textBoxes,
      }};
    }})()
    """

    for style_name in CANONICAL_STYLE_ORDER:
        html = generate_scene_html(
            scene_text=scene_text,
            style=style_name,
            brand=brand,
            duration_s=duration_s,
            words=words,
        )
        metrics = _evaluate_in_chrome(html, tmp_path, inspect_js, name=f"center_{style_name}.html")
        assert "error" not in metrics, f"{style_name}: {metrics}"

        sr = metrics["stageRect"]
        assert sr["left"] == pytest.approx(STAGE_LEFT, abs=1.0)
        assert sr["top"] == pytest.approx(STAGE_TOP, abs=1.0)
        assert sr["right"] == pytest.approx(STAGE_RIGHT, abs=1.0)
        assert sr["bottom"] == pytest.approx(STAGE_BOTTOM, abs=1.0)

        assert len(metrics["children"]) >= 2
        for ch in metrics["children"]:
            offset = abs(ch["centerX"] - STAGE_CENTER_X)
            assert offset <= 4.0, (
                f"{style_name} top-level child {ch['primitive']} ({ch['id']}) off-center: "
                f"centerX={ch['centerX']:.2f} (|center-540|={offset:.2f} > 4px)"
            )

        for node in metrics["keyNodes"]:
            offset = abs(node["centerX"] - STAGE_CENTER_X)
            assert offset <= 4.0, (
                f"{style_name} element {node['cls']!r} off-center: centerX={node['centerX']:.2f}"
            )

        assert len(metrics["textBoxes"]) >= 2
        for tb in metrics["textBoxes"]:
            assert tb["bottom"] <= STAGE_BOTTOM + 1.0, (
                f"{style_name} text {tb['text']!r} bottom={tb['bottom']:.1f} > {STAGE_BOTTOM}"
            )
            assert tb["left"] >= STAGE_LEFT - 1.0, (
                f"{style_name} text {tb['text']!r} left={tb['left']:.1f} < {STAGE_LEFT}"
            )
            assert tb["right"] <= STAGE_RIGHT + 1.0, (
                f"{style_name} text {tb['text']!r} right={tb['right']:.1f} > {STAGE_RIGHT}"
            )
            assert tb["top"] >= STAGE_TOP - 1.0, (
                f"{style_name} text {tb['text']!r} top={tb['top']:.1f} < {STAGE_TOP}"
            )

        audit = audit_layout(tmp_path / f"center_{style_name}.html", duration_s=duration_s)
        assert audit.passed, f"{style_name} failed audit_layout: {audit.violations}"


# ---------------------------------------------------------------------------
# 2. kinetic-type: every KineticWordStack row computed font-size >= 96px
# ---------------------------------------------------------------------------


@requires_chrome
def test_kinetic_type_every_row_computed_font_size_at_least_96px(tmp_path: Path) -> None:
    brand = _sample_malwarebytes_brand()
    html = generate_scene_html(
        scene_text="Before you answer a call or click a link, Scam Guard checks if it is a scam.",
        style="kinetic-type",
        brand=brand,
        duration_s=4.0,
    )
    js_check = """
    (() => {
      if (typeof window.__seek === 'function') window.__seek(4.0);
      const rows = Array.from(document.querySelectorAll('.kinetic-row'));
      return rows.map((r) => {
        const st = window.getComputedStyle(r);
        const rect = r.getBoundingClientRect();
        return {
          text: (r.textContent || '').trim(),
          fontSize: parseFloat(st.fontSize),
          opacity: parseFloat(st.opacity),
          centerX: (rect.left + rect.right) / 2.0,
          left: rect.left,
          right: rect.right,
          bottom: rect.bottom,
        };
      });
    })()
    """
    rows = _evaluate_in_chrome(html, tmp_path, js_check, name="kinetic_rows.html")
    assert 2 <= len(rows) <= 4
    for row in rows:
        assert row["fontSize"] >= 96.0, f"Kinetic row {row['text']!r} font-size {row['fontSize']}px < 96px"
        assert row["opacity"] >= 0.99
        assert abs(row["centerX"] - STAGE_CENTER_X) <= 4.0
        assert row["left"] >= STAGE_LEFT - 1.0
        assert row["right"] <= STAGE_RIGHT + 1.0
        assert row["bottom"] <= STAGE_BOTTOM + 1.0


# ---------------------------------------------------------------------------
# 3. Contrast: BrandHeader wordmark vs effective bg >= 4.5 & on-surface/on-accent >= 4.5
# ---------------------------------------------------------------------------


@requires_chrome
def test_brand_header_and_token_contrast_across_all_styles_light_and_dark_brands(tmp_path: Path) -> None:
    sample_brand = _sample_malwarebytes_brand()
    dark_brand = {
        "name": "NightShield",
        "palette": {
            "primary": "#2563EB",
            "secondary": "#14B8A6",
            "bg": "#090D16",
            "ink": "#001A4D",  # dark navy ink on dark bg -> must trigger contrasting pill plate / white text
        },
    }

    for brand_label, brand_input in (("sample_mb", sample_brand), ("dark_bg", dark_brand)):
        for style_name in CANONICAL_STYLE_ORDER:
            preset = load_style_preset(style_name)
            norm = normalize_brand(brand_input, preset)
            pal = norm["palette"]

            assert wcag_contrast_ratio(pal["on_bg"], pal["bg"]) >= 4.5
            assert wcag_contrast_ratio(pal["on_surface"], pal["surface"]) >= 4.5
            assert wcag_contrast_ratio(pal["on_accent"], pal["accent"]) >= 4.5
            assert wcag_contrast_ratio(pal["on_accent2"], pal["accent2"]) >= 4.5

            hc = resolve_brand_header_contrast(norm, logo_plate="auto")
            ratio = wcag_contrast_ratio(hc["wordmark_color"], hc["effective_bg"])
            assert ratio >= 4.5, (
                f"{brand_label}/{style_name}: BrandHeader wordmark {hc['wordmark_color']} vs "
                f"effective_bg {hc['effective_bg']} has contrast {ratio:.2f} < 4.5"
            )
            if style_name == "glass-ui" and brand_label == "sample_mb":
                # glass-ui forces a dark canvas (#0A1628) so Malwarebytes's dark navy (#001A4D)
                # logo/wordmark MUST sit on a contrasting white pill plate
                assert hc["has_plate"] is True
                assert hc["effective_bg"].upper() == "#FFFFFF"

            html = generate_scene_html(
                "Real-time scam call blocking.",
                style_name,
                brand=brand_input,
                duration_s=2.0,
            )
            dom_colors = _evaluate_in_chrome(
                html,
                tmp_path,
                """
                (() => {
                  window.__seek(2.0);
                  const plate = document.querySelector('.brand-plate');
                  const wm = document.querySelector('.brand-wordmark');
                  return {
                    effectiveBg: plate ? plate.getAttribute('data-effective-bg') : null,
                    wordmarkColor: wm ? wm.getAttribute('data-wordmark-color') : null,
                    hasPlate: plate ? plate.getAttribute('data-has-plate') : null,
                  };
                })()
                """,
                name=f"contrast_{brand_label}_{style_name}.html",
            )
            assert dom_colors["effectiveBg"]
            assert dom_colors["wordmarkColor"]
            assert wcag_contrast_ratio(dom_colors["wordmarkColor"], dom_colors["effectiveBg"]) >= 4.5


# ---------------------------------------------------------------------------
# 4. flat-explainer headline opacity ~1 at t=0.6s regardless of late cue
# ---------------------------------------------------------------------------


@requires_chrome
def test_flat_explainer_headline_opacity_one_by_0_6s_even_with_late_cue(tmp_path: Path) -> None:
    late_words = [
        {"word": "lateword", "start": 3.2, "end": 3.8},
        {"word": "secondword", "start": 4.1, "end": 4.7},
    ]
    spec = build_default_scene_spec(
        "Scam Guard blocks suspicious calls before your phone even rings.",
        style="flat-explainer",
        words=late_words,
    )
    # Explicitly attach the late cue to every primitive to verify the <=0.6s clamp rule
    for prim in spec["primitives"]:
        prim.setdefault("motion", {})["cue"] = "lateword"

    html = render_scene_spec_html(
        spec,
        style="flat-explainer",
        brand=_sample_malwarebytes_brand(),
        duration_s=5.0,
        words=late_words,
    )
    opacities = _evaluate_in_chrome(
        html,
        tmp_path,
        """
        (() => {
          window.__seek(0.6);
          const hl = document.querySelector('.headline-block, .feature-card, .kinetic-row');
          const brand = document.querySelector('.brand-header');
          const hlOpacityAt06 = hl ? parseFloat(window.getComputedStyle(hl).opacity) : 0;
          const brandOpacityAt06 = brand ? parseFloat(window.getComputedStyle(brand).opacity) : 0;
          window.__seek(5.0);
          const hlOpacityFinal = hl ? parseFloat(window.getComputedStyle(hl).opacity) : 0;
          return { hlOpacityAt06, brandOpacityAt06, hlOpacityFinal };
        })()
        """,
        name="flat_early_headline.html",
    )
    assert opacities["hlOpacityAt06"] >= 0.99, f"Expected headline opacity ~1 at t=0.6s, got {opacities}"
    assert opacities["brandOpacityAt06"] >= 0.99
    assert opacities["hlOpacityFinal"] >= 0.99


# ---------------------------------------------------------------------------
# 5. Spec validation clamps lengths, drops unknown primitives/icons, and LLM repair -> fallback
# ---------------------------------------------------------------------------


def test_scene_spec_validation_clamps_lengths_caps_children_and_drops_unknowns() -> None:
    raw_spec = {
        "style": "flat-explainer",
        "layout_slot": "unknown-slot",
        "gap": "huge",
        "bg_variant": "unknown-bg",
        "primitives": [
            {"type": "UnknownMagicWidget", "props": {"foo": "bar"}},
            {
                "type": "BrandHeader",
                "props": {
                    "tag_text": "THIS TAG IS WAY TOO LONG FOR THE PILL HEADER",
                    "wordmark": "A" * 50,
                },
            },
            {
                "type": "HeadlineBlock",
                "props": {
                    "eyebrow": "E" * 45,
                    "headline": "H" * 90,
                    "subhead": "S" * 200,
                    "size": "gigantic",
                },
            },
            {
                "type": "IconBadge",
                "props": {"name": "nonexistent-ufo-icon", "size": "lg"},
            },
            {
                "type": "KineticWordStack",
                "props": {
                    "rows": [
                        {"text": "SUPERLONGUNBROKENWORD1234567890", "style": "pill-accent"},
                        {"text": "ROW TWO TEXT IS VERY LONG INDEED EXTRAS", "style": "invalid-style"},
                        {"text": "ROW THREE", "style": "outline"},
                        {"text": "ROW FOUR", "style": "plain"},
                        {"text": "ROW FIVE DROPPED BY CAP", "style": "plain"},
                    ]
                },
            },
            {
                "type": "PhoneFrame",
                "props": {
                    "header_label": "L" * 45,
                    "children": [
                        {"type": "UnknownChild", "props": {}},
                        {"type": "IconBadge", "props": {"name": "bad-icon"}},
                        {"type": "PillBadge", "props": {"label": "P" * 45, "icon": "bad-icon"}},
                        {"type": "FeatureCard", "props": {"title": "T" * 60, "body": "B" * 140, "icon": "shield"}},
                        {"type": "CtaButton", "props": {"label": "C" * 50, "icon": "check"}},
                        {"type": "StatCallout", "props": {"value": "99.9%", "label": "FOURTH KEPT"}},
                        {"type": "ChecklistStack", "props": {"items": ["FIFTH EXTRACHILD DROPPED"]}},
                    ],
                },
            },
            {
                "type": "CtaButton",
                "props": {"label": "SIXTH STAGE PRIMITIVE DROPPED BY CAP"},
            },
        ],
    }

    cleaned = validate_scene_spec(raw_spec, style="flat-explainer", strict=True, ensure_brand_header=True)
    assert cleaned["layout_slot"] == "phone-hero"
    assert cleaned["gap"] == "md"
    assert cleaned["bg_variant"] == "radial"
    assert len(cleaned["primitives"]) <= CHILD_CAPS["stage_primitives"][1]

    types = [p["type"] for p in cleaned["primitives"]]
    assert "UnknownMagicWidget" not in types
    assert "IconBadge" not in types  # dropped because icon was unknown

    bh = next(p for p in cleaned["primitives"] if p["type"] == "BrandHeader")
    assert len(bh["props"]["tag_text"]) <= CHAR_LIMITS["BrandHeader"]["tag_text"]
    assert len(bh["props"]["wordmark"]) <= CHAR_LIMITS["BrandHeader"]["wordmark"]

    hb = next(p for p in cleaned["primitives"] if p["type"] == "HeadlineBlock")
    assert len(hb["props"]["eyebrow"]) <= CHAR_LIMITS["HeadlineBlock"]["eyebrow"]
    assert len(hb["props"]["headline"]) <= CHAR_LIMITS["HeadlineBlock"]["headline"]
    assert len(hb["props"]["subhead"]) <= CHAR_LIMITS["HeadlineBlock"]["subhead"]
    assert hb["props"]["size"] == "lg"

    ks = next(p for p in cleaned["primitives"] if p["type"] == "KineticWordStack")
    rows = ks["props"]["rows"]
    assert len(rows) == CHILD_CAPS["KineticWordStack"][1]
    for r in rows:
        assert len(r["text"]) <= CHAR_LIMITS["KineticWordStack"]["row_text"]
        for tok in r["text"].split():
            assert len(tok) <= CHAR_LIMITS["KineticWordStack"]["max_single_word"]

    pf = next(p for p in cleaned["primitives"] if p["type"] == "PhoneFrame")
    assert len(pf["props"]["header_label"]) <= CHAR_LIMITS["PhoneFrame"]["header_label"]
    assert len(pf["props"]["children"]) == CHILD_CAPS["PhoneFrame"][1]
    pill = pf["props"]["children"][0]
    assert pill["type"] == "PillBadge"
    assert "icon" not in pill["props"]  # unknown icon stripped
    assert len(pill["props"]["label"]) <= CHAR_LIMITS["PillBadge"]["label"]


def test_real_mode_invalid_llm_json_triggers_one_repair_then_deterministic_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PARALLAX_TEST_MODE", "0")

    calls: list[dict[str, Any]] = []

    # Case A: 1st call returns broken JSON, 2nd call (repair) returns code-fenced valid SceneSpec
    valid_spec = {
        "style": "glass-ui",
        "layout_slot": "phone-hero",
        "gap": "md",
        "bg_variant": "orb-glow",
        "primitives": [
            {"type": "BrandHeader", "props": {"logo_plate": "auto"}},
            {
                "type": "HeadlineBlock",
                "props": {"headline": "Repaired Spec Works", "size": "lg"},
                "motion": {"entrance": "fade-up", "cue": "scam"},
            },
        ],
    }
    responses_a = iter(
        [
            "not valid json {{{",
            f"```json\n{json.dumps(valid_spec)}\n```",
        ]
    )

    def _mock_post_repair_succeeds(path: str, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(body)
        return {
            "id": f"gen-spec-{len(calls)}",
            "choices": [{"message": {"content": next(responses_a)}}],
            "usage": {"prompt_tokens": 400, "completion_tokens": 300, "cost": 0.0009},
        }

    monkeypatch.setattr(openrouter_client, "_post", _mock_post_repair_succeeds)
    spec_a, html_a = generate_scene_spec_and_html(
        "Scam Guard blocks threats.",
        "glass-ui",
        brand=_sample_malwarebytes_brand(),
        duration_s=3.0,
    )
    assert len(calls) == 2
    assert calls[0].get("response_format") == {"type": "json_object"}
    assert "Invalid JSON" in calls[1]["messages"][1]["content"]
    assert spec_a["primitives"][1]["props"]["headline"] == "Repaired Spec Works"
    assert "Repaired Spec Works" in html_a

    # Case B: Both initial call and repair call return invalid JSON/schema -> falls back to deterministic spec
    calls.clear()
    responses_b = iter(
        [
            "still not json <<<",
            json.dumps({"style": "neo-brutal", "primitives": [{"type": "UnknownPrimitiveOnly"}]}),
        ]
    )

    def _mock_post_both_fail(path: str, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(body)
        return {
            "id": f"gen-fallback-{len(calls)}",
            "choices": [{"message": {"content": next(responses_b)}}],
            "usage": {"prompt_tokens": 400, "completion_tokens": 300, "cost": 0.0009},
        }

    monkeypatch.setattr(openrouter_client, "_post", _mock_post_both_fail)
    spec_b, html_b = generate_scene_spec_and_html(
        "Deterministic fallback copy keeps pipeline alive.",
        "neo-brutal",
        brand=_sample_malwarebytes_brand(),
        duration_s=3.0,
    )
    assert len(calls) == 2
    assert spec_b["style"] == "neo-brutal"
    assert len(spec_b["primitives"]) >= 2
    assert 'id="safe-stage"' in html_b


# ---------------------------------------------------------------------------
# 6. `--from-spec` CLI re-render works with zero network calls ($0)
# ---------------------------------------------------------------------------


@requires_chrome
def test_cli_saves_spec_json_and_rerenders_from_spec_with_zero_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        openrouter_client,
        "_post",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("OpenRouter _post must not be called")),
    )

    out_v1 = tmp_path / "glass_s2.mp4"
    rc1 = cli.main(
        [
            "video",
            "motion",
            "--style",
            "glass-ui",
            "--text",
            "Before you answer a call, Scam Guard checks if it is safe.",
            "--duration",
            "1.0",
            "--fps",
            "6",
            "--out",
            str(out_v1),
            "--json",
        ]
    )
    assert rc1 == 0
    payload1 = json.loads(capsys.readouterr().out)
    spec_v1 = Path(payload1["spec_path"])
    assert spec_v1 == out_v1.with_suffix(".spec.json")
    assert spec_v1.is_file()
    assert out_v1.with_suffix(".html").is_file()
    assert out_v1.is_file()

    # Modify headline in the saved spec and re-render via --from-spec at $0
    spec_data = json.loads(spec_v1.read_text(encoding="utf-8"))
    for prim in spec_data["primitives"]:
        if prim["type"] == "HeadlineBlock":
            prim["props"]["headline"] = "Custom Spec Re-Render"
    custom_spec_path = tmp_path / "custom.spec.json"
    custom_spec_path.write_text(json.dumps(spec_data, indent=2), encoding="utf-8")

    out_v2 = tmp_path / "rerendered.mp4"
    rc2 = cli.main(
        [
            "video",
            "motion",
            "--from-spec",
            str(custom_spec_path),
            "--duration",
            "1.5",
            "--fps",
            "6",
            "--out",
            str(out_v2),
            "--json",
        ]
    )
    assert rc2 == 0
    payload2 = json.loads(capsys.readouterr().out)
    assert payload2["retime_only"] is True
    assert out_v2.is_file()
    assert out_v2.with_suffix(".html").is_file()
    assert out_v2.with_suffix(".spec.json").is_file()
    assert "Custom Spec Re-Render" in out_v2.with_suffix(".html").read_text(encoding="utf-8")

    probe = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_entries",
                "stream=width,height,nb_read_frames",
                "-of",
                "json",
                str(out_v2),
            ],
            text=True,
        )
    )
    stream = probe["streams"][0]
    assert int(stream["width"]) == 1080
    assert int(stream["height"]) == 1920
    assert int(stream["nb_read_frames"]) == 9


# ---------------------------------------------------------------------------
# 7. Regression: nested primitives in PhoneFrame / LayoutSlot never overflow parent or shift off-center
# ---------------------------------------------------------------------------


@requires_chrome
def test_nested_primitives_in_phone_frame_never_overflow_and_stay_centered(tmp_path: Path) -> None:
    brand = _sample_malwarebytes_brand()
    duration_s = 4.0

    inspect_phone_js = f"""
    (() => {{
      if (typeof window.__seek === 'function') window.__seek({duration_s});
      const stage = document.getElementById('safe-stage');
      const phone = document.querySelector('.phone-frame');
      const phoneBody = document.querySelector('.phone-body');
      if (!stage || !phone || !phoneBody) {{
        return {{ error: 'missing stage/phone/phoneBody' }};
      }}
      const phoneRect = phone.getBoundingClientRect();
      const stageChildren = Array.from(stage.children).map((el) => {{
        const r = el.getBoundingClientRect();
        return {{
          id: el.id,
          primitive: el.getAttribute('data-primitive') || el.tagName,
          left: r.left,
          right: r.right,
          top: r.top,
          bottom: r.bottom,
          centerX: (r.left + r.right) / 2.0,
        }};
      }});
      const phoneChildren = Array.from(phoneBody.children).map((el) => {{
        const r = el.getBoundingClientRect();
        return {{
          id: el.id,
          primitive: el.getAttribute('data-primitive') || el.tagName,
          left: r.left,
          right: r.right,
          top: r.top,
          bottom: r.bottom,
          width: r.width,
          centerX: (r.left + r.right) / 2.0,
        }};
      }});
      const ignored = new Set(['SCRIPT', 'STYLE', 'TITLE', 'HEAD', 'META', 'LINK', 'DEFS', 'DESC']);
      function collectTextBoxes(rootEl) {{
        const out = [];
        for (const el of Array.from(rootEl.querySelectorAll('*'))) {{
          if (ignored.has(el.tagName.toUpperCase())) continue;
          let hasText = false;
          for (let i = 0; i < el.childNodes.length; i++) {{
            const n = el.childNodes[i];
            if (n.nodeType === 3 && n.nodeValue && n.nodeValue.trim().length > 0) {{
              hasText = true;
              break;
            }}
          }}
          if (!hasText) continue;
          const st = window.getComputedStyle(el);
          if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) <= 0.05) continue;
          const r = el.getBoundingClientRect();
          if (r.width <= 0.5 || r.height <= 0.5) continue;
          out.push({{
            text: (el.textContent || '').trim().slice(0, 50),
            left: r.left,
            right: r.right,
            top: r.top,
            bottom: r.bottom,
            centerX: (r.left + r.right) / 2.0,
          }});
        }}
        return out;
      }}
      return {{
        phoneRect: {{
          left: phoneRect.left,
          right: phoneRect.right,
          top: phoneRect.top,
          bottom: phoneRect.bottom,
          centerX: (phoneRect.left + phoneRect.right) / 2.0,
        }},
        stageChildren,
        phoneChildren,
        phoneTextBoxes: collectTextBoxes(phone),
        allTextBoxes: collectTextBoxes(document.body),
      }};
    }})()
    """

    for style_name in CANONICAL_STYLE_ORDER:
        # Spec 1: BrandHeader + PhoneFrame[PillBadge, HeadlineBlock(size lg, 'Is it a scam?'), FeatureCard, CtaButton]
        spec_stack = {
            "style": style_name,
            "layout_slot": "phone-hero",
            "gap": "md",
            "bg_variant": "solid",
            "primitives": [
                {"type": "BrandHeader", "props": {"align": "center", "logo_plate": "auto"}},
                {
                    "type": "PhoneFrame",
                    "props": {
                        "header_label": "Incoming Call",
                        "children": [
                            {
                                "type": "PillBadge",
                                "props": {"label": "Unknown Number", "tone": "accent2", "pulse": True, "icon": "phone-call"},
                            },
                            {
                                "type": "HeadlineBlock",
                                "props": {"headline": "Is it a scam?", "size": "lg", "align": "center"},
                            },
                            {
                                "type": "FeatureCard",
                                "props": {
                                    "title": "AI Call Screening",
                                    "body": "Checks suspicious callers before your phone rings.",
                                    "icon": "shield",
                                    "tone": "surface",
                                },
                            },
                            {
                                "type": "CtaButton",
                                "props": {"label": "Block Scam Call", "tone": "ink", "icon": "check"},
                            },
                        ],
                    },
                },
            ],
        }

        # Spec 2: HeadlineBlock size hero inside PhoneFrame with a long headline (plus StatCallout & ChecklistStack)
        spec_hero_long = {
            "style": style_name,
            "layout_slot": "phone-hero",
            "gap": "md",
            "bg_variant": "radial",
            "primitives": [
                {"type": "BrandHeader", "props": {"align": "center", "logo_plate": "auto"}},
                {
                    "type": "PhoneFrame",
                    "props": {
                        "header_label": "Scam Guard Active",
                        "children": [
                            {
                                "type": "HeadlineBlock",
                                "props": {
                                    "eyebrow": "REAL-TIME PROTECTION",
                                    "headline": "Suspicious Scam Caller Blocked Automatically",
                                    "subhead": "Malwarebytes screens unknown numbers and risky links before you answer.",
                                    "size": "hero",
                                    "align": "center",
                                },
                            },
                            {
                                "type": "StatCallout",
                                "props": {"value": "99.9%", "label": "SCAM CALLS BLOCKED", "delta": "+24% FASTER", "meter": "bar"},
                            },
                            {
                                "type": "ChecklistStack",
                                "props": {
                                    "items": [
                                        {"label": "Caller identity verified instantly", "state": "check"},
                                        {"label": "Phishing links quarantined", "state": "check"},
                                    ]
                                },
                            },
                        ],
                    },
                },
            ],
        }

        for variant_name, spec in (("stack4", spec_stack), ("hero_long", spec_hero_long)):
            html = render_scene_spec_html(
                spec,
                style=style_name,
                brand=brand,
                duration_s=duration_s,
            )
            fname = f"phone_{variant_name}_{style_name}.html"
            metrics = _evaluate_in_chrome(html, tmp_path, inspect_phone_js, name=fname)
            assert "error" not in metrics, f"{style_name}/{variant_name}: {metrics}"

            pr = metrics["phoneRect"]
            assert abs(pr["centerX"] - STAGE_CENTER_X) <= 4.0, (
                f"{style_name}/{variant_name} PhoneFrame off-center: centerX={pr['centerX']:.2f}"
            )

            for sc in metrics["stageChildren"]:
                assert abs(sc["centerX"] - STAGE_CENTER_X) <= 4.0, (
                    f"{style_name}/{variant_name} stage child {sc['primitive']} off-center: centerX={sc['centerX']:.2f}"
                )

            assert len(metrics["phoneChildren"]) >= 3
            for pc in metrics["phoneChildren"]:
                assert abs(pc["centerX"] - STAGE_CENTER_X) <= 4.0, (
                    f"{style_name}/{variant_name} phone child {pc['primitive']} off-center: centerX={pc['centerX']:.2f}"
                )
                assert pc["left"] >= pr["left"] - 1.0, (
                    f"{style_name}/{variant_name} phone child {pc['primitive']} overflows phone left: "
                    f"{pc['left']:.1f} < {pr['left']:.1f}"
                )
                assert pc["right"] <= pr["right"] + 1.0, (
                    f"{style_name}/{variant_name} phone child {pc['primitive']} overflows phone right: "
                    f"{pc['right']:.1f} > {pr['right']:.1f}"
                )

            assert len(metrics["phoneTextBoxes"]) >= 2
            for tb in metrics["phoneTextBoxes"]:
                assert tb["left"] >= pr["left"] - 1.0, (
                    f"{style_name}/{variant_name} phone text {tb['text']!r} left={tb['left']:.1f} < phone.left={pr['left']:.1f}"
                )
                assert tb["right"] <= pr["right"] + 1.0, (
                    f"{style_name}/{variant_name} phone text {tb['text']!r} right={tb['right']:.1f} > phone.right={pr['right']:.1f}"
                )

            for tb in metrics["allTextBoxes"]:
                assert tb["left"] >= STAGE_LEFT - 1.0, (
                    f"{style_name}/{variant_name} text {tb['text']!r} left={tb['left']:.1f} < {STAGE_LEFT}"
                )
                assert tb["right"] <= STAGE_RIGHT + 1.0, (
                    f"{style_name}/{variant_name} text {tb['text']!r} right={tb['right']:.1f} > {STAGE_RIGHT}"
                )
                assert tb["bottom"] <= STAGE_BOTTOM + 1.0, (
                    f"{style_name}/{variant_name} text {tb['text']!r} bottom={tb['bottom']:.1f} > {STAGE_BOTTOM}"
                )
                assert tb["top"] >= STAGE_TOP - 1.0, (
                    f"{style_name}/{variant_name} text {tb['text']!r} top={tb['top']:.1f} < {STAGE_TOP}"
                )

            audit = audit_layout(tmp_path / fname, duration_s=duration_s)
            assert audit.passed, f"{style_name}/{variant_name} failed audit_layout: {audit.violations}"


@requires_chrome
def test_recovered_real3_rejected_specs_render_cleanly_and_pass_audit(tmp_path: Path) -> None:
    brand = _sample_malwarebytes_brand()
    recovered_specs = [
        (
            "flat-explainer-s1",
            {
                "style": "flat-explainer",
                "layout_slot": "phone-hero",
                "gap": "md",
                "bg_variant": "solid",
                "primitives": [
                    {
                        "type": "BrandHeader",
                        "props": {"align": "center", "logo_plate": "auto"},
                        "motion": {"entrance": "fade-up", "emphasis": "none", "cue": "your"},
                    },
                    {
                        "type": "PhoneFrame",
                        "props": {
                            "header_label": "Incoming Call",
                            "bezel": "preset",
                            "children": [
                                {
                                    "type": "PillBadge",
                                    "props": {
                                        "label": "Unknown Number",
                                        "tone": "accent2",
                                        "pulse": True,
                                        "icon": "phone-call",
                                    },
                                    "motion": {"entrance": "pop", "emphasis": "pulse", "cue": "phone"},
                                },
                                {
                                    "type": "HeadlineBlock",
                                    "props": {"headline": "Is it a scam?", "size": "lg", "align": "center"},
                                    "motion": {"entrance": "slide-up", "emphasis": "none", "cue": "scam"},
                                },
                            ],
                        },
                        "motion": {"entrance": "pop", "emphasis": "none", "cue": "rings"},
                    },
                ],
            },
        ),
        (
            "neo-brutal-s1",
            {
                "style": "neo-brutal",
                "layout_slot": "phone-hero",
                "gap": "md",
                "bg_variant": "solid",
                "primitives": [
                    {
                        "type": "BrandHeader",
                        "props": {"align": "center", "logo_plate": "auto"},
                        "motion": {"entrance": "fade-up", "emphasis": "none", "cue": ""},
                    },
                    {
                        "type": "PhoneFrame",
                        "props": {
                            "header_label": "Incoming Call",
                            "bezel": "preset",
                            "children": [
                                {
                                    "type": "IconBadge",
                                    "props": {
                                        "name": "phone-call",
                                        "size": "lg",
                                        "tone": "accent",
                                        "draw_stroke": True,
                                    },
                                    "motion": {"entrance": "pop", "emphasis": "none", "cue": "rings"},
                                },
                                {
                                    "type": "HeadlineBlock",
                                    "props": {
                                        "headline": "UNKNOWN",
                                        "size": "md",
                                        "align": "center",
                                        "subhead": "CALLER",
                                    },
                                    "motion": {"entrance": "slide-up", "emphasis": "none", "cue": "caller"},
                                },
                                {
                                    "type": "PillBadge",
                                    "props": {
                                        "label": "IS IT A SCAM?",
                                        "tone": "ink",
                                        "pulse": True,
                                        "icon": "alert",
                                    },
                                    "motion": {"entrance": "stamp", "emphasis": "none", "cue": "scam"},
                                },
                            ],
                        },
                        "motion": {"entrance": "pop", "emphasis": "none", "cue": "phone"},
                    },
                ],
            },
        ),
        (
            "glass-ui-s1",
            {
                "style": "glass-ui",
                "layout_slot": "phone-hero",
                "gap": "md",
                "bg_variant": "orb-glow",
                "primitives": [
                    {
                        "type": "BrandHeader",
                        "props": {"align": "center", "logo_plate": "auto", "tag_text": "Malwarebytes"},
                        "motion": {"entrance": "fade-up", "emphasis": "none", "cue": ""},
                    },
                    {
                        "type": "PhoneFrame",
                        "props": {
                            "header_label": "Incoming Call",
                            "bezel": "preset",
                            "children": [
                                {
                                    "type": "IconBadge",
                                    "props": {
                                        "name": "phone-call",
                                        "size": "lg",
                                        "tone": "accent",
                                        "draw_stroke": True,
                                    },
                                    "motion": {"entrance": "pop", "emphasis": "pulse", "cue": "rings"},
                                },
                                {
                                    "type": "HeadlineBlock",
                                    "props": {"headline": "Scam Likely", "size": "md", "align": "center"},
                                    "motion": {"entrance": "fade-up", "emphasis": "none", "cue": "unknown"},
                                },
                                {
                                    "type": "CtaButton",
                                    "props": {
                                        "label": "Block Call",
                                        "tone": "ink",
                                        "emphasis": "pulse",
                                        "icon": "alert",
                                    },
                                    "motion": {"entrance": "slide-up", "emphasis": "pulse", "cue": "scam"},
                                },
                            ],
                        },
                        "motion": {"entrance": "slide-up", "emphasis": "none", "cue": "phone"},
                    },
                ],
            },
        ),
        (
            "glass-ui-s3",
            {
                "style": "glass-ui",
                "layout_slot": "phone-hero",
                "gap": "md",
                "bg_variant": "orb-glow",
                "primitives": [
                    {
                        "type": "BrandHeader",
                        "props": {"align": "center", "logo_plate": "auto", "tag_text": "Malwarebytes"},
                        "motion": {"entrance": "fade-up", "emphasis": "none", "cue": ""},
                    },
                    {
                        "type": "PhoneFrame",
                        "props": {
                            "header_label": "Malwarebytes",
                            "bezel": "preset",
                            "children": [
                                {
                                    "type": "IconBadge",
                                    "props": {
                                        "name": "shield",
                                        "size": "lg",
                                        "tone": "accent",
                                        "draw_stroke": True,
                                    },
                                    "motion": {"entrance": "draw", "emphasis": "none", "cue": "scam"},
                                },
                                {
                                    "type": "HeadlineBlock",
                                    "props": {"headline": "Scam Blocked!", "size": "md", "align": "center"},
                                    "motion": {"entrance": "pop", "emphasis": "none", "cue": "blocked"},
                                },
                                {
                                    "type": "CtaButton",
                                    "props": {
                                        "label": "Try Free Now",
                                        "tone": "accent",
                                        "emphasis": "pulse",
                                        "icon": "check",
                                    },
                                    "motion": {"entrance": "slide-up", "emphasis": "pulse", "cue": "today"},
                                },
                            ],
                        },
                        "motion": {"entrance": "pop", "emphasis": "none", "cue": ""},
                    },
                ],
            },
        ),
        (
            "editorial-clinical-s1",
            {
                "style": "editorial-clinical",
                "layout_slot": "phone-hero",
                "gap": "md",
                "bg_variant": "solid",
                "primitives": [
                    {
                        "type": "BrandHeader",
                        "props": {"align": "split", "logo_plate": "auto", "tag_text": "Malwarebytes"},
                        "motion": {"entrance": "fade-up", "emphasis": "none", "cue": ""},
                    },
                    {
                        "type": "PhoneFrame",
                        "props": {
                            "header_label": "Incoming Call",
                            "bezel": "preset",
                            "children": [
                                {
                                    "type": "IconBadge",
                                    "props": {
                                        "name": "phone-call",
                                        "size": "lg",
                                        "tone": "accent",
                                        "draw_stroke": True,
                                    },
                                    "motion": {"entrance": "pop", "emphasis": "pulse", "cue": "rings"},
                                },
                                {
                                    "type": "HeadlineBlock",
                                    "props": {
                                        "headline": "Unknown Number?",
                                        "size": "md",
                                        "align": "center",
                                        "subhead": "Is it a scam?",
                                    },
                                    "motion": {"entrance": "slide-up", "emphasis": "none", "cue": "unknown"},
                                },
                                {
                                    "type": "CtaButton",
                                    "props": {
                                        "label": "Block Call",
                                        "tone": "ink",
                                        "emphasis": "none",
                                        "icon": "x",
                                    },
                                    "motion": {"entrance": "fade-up", "emphasis": "none", "cue": "scam"},
                                },
                            ],
                        },
                        "motion": {"entrance": "slide-up", "emphasis": "none", "cue": "phone"},
                    },
                ],
            },
        ),
    ]

    for scene_label, spec in recovered_specs:
        html = render_scene_spec_html(
            spec,
            style=spec["style"],
            brand=brand,
            duration_s=3.6,
        )
        out_html = tmp_path / f"{scene_label}.html"
        out_html.write_text(html, encoding="utf-8")
        audit = audit_layout(out_html, duration_s=3.6)
        assert audit.passed, f"{scene_label} failed audit_layout: {audit.violations}"


# ---------------------------------------------------------------------------
# 8. Auto-fit fill ratio [0.78, 0.92], centering around y=700, effective font floors, sparse phone >= 600, and dense cap
# ---------------------------------------------------------------------------

_STACK_METRICS_JS = """
(() => {
  if (typeof window.__seek === 'function') window.__seek(5.0);
  const stage = document.getElementById('safe-stage');
  if (!stage) return { error: 'missing #safe-stage' };
  const children = Array.from(stage.children);
  let minT = Infinity, maxB = -Infinity, minL = Infinity, maxR = -Infinity;
  const cxDiffs = [];
  for (const c of children) {
    const r = c.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    minT = Math.min(minT, r.top);
    maxB = Math.max(maxB, r.bottom);
    minL = Math.min(minL, r.left);
    maxR = Math.max(maxR, r.right);
    cxDiffs.push(Math.abs(((r.left + r.right) / 2.0) - 540.0));
  }
  const height = maxB - minT;
  const fillRatio = height / 1240.0;
  const stageCenterY = (80.0 + 1320.0) / 2.0;
  const stackCenterY = (minT + maxB) / 2.0;

  function effZoom(el) {
    let z = 1.0;
    let cur = el;
    while (cur && cur !== document.body && cur !== document.documentElement) {
      const sz = parseFloat(cur.style && cur.style.zoom);
      if (!isNaN(sz) && sz > 0) z *= sz;
      cur = cur.parentElement;
    }
    return z;
  }

  function effFont(el) {
    return parseFloat(window.getComputedStyle(el).fontSize) * effZoom(el);
  }

  const phone = stage.querySelector('.phone-frame');
  const phoneBody = stage.querySelector('.phone-body');
  const phoneRect = phone ? phone.getBoundingClientRect() : null;
  const phoneChildCount = phoneBody ? phoneBody.children.length : null;
  const zoom = parseFloat(stage.getAttribute('data-auto-fit-zoom') || '1') || 1.0;

  const headlineSizes = Array.from(stage.querySelectorAll('.headline-main')).map(effFont);
  const heroHeadlineSizes = Array.from(stage.querySelectorAll('.headline-block.size-hero .headline-main')).map(effFont);
  const titleSizes = Array.from(stage.querySelectorAll('.card-title')).map(effFont);
  const ctaSizes = Array.from(stage.querySelectorAll('.cta-label, .cta-button')).map(effFont);
  const phoneHeaderSizes = Array.from(stage.querySelectorAll('.phone-header-label')).map(effFont);
  const eyebrowSizes = Array.from(stage.querySelectorAll('.headline-eyebrow')).map(effFont);
  const bodySizes = Array.from(
    stage.querySelectorAll('.card-sub, .headline-sub, .pill-badge, .pill-label, .checklist-item, .checklist-label, .stat-label, .stat-delta, .brand-tag')
  ).map(effFont);
  const kineticSizes = Array.from(stage.querySelectorAll('.kinetic-row')).map(effFont);
  const iconBadgeRects = Array.from(stage.querySelectorAll('.icon-badge')).map((b) => {
    const r = b.getBoundingClientRect();
    return { width: r.width, height: r.height };
  });

  const cssHeadlineSizes = Array.from(stage.querySelectorAll('.headline-main')).map(
    (el) => parseFloat(window.getComputedStyle(el).fontSize)
  );
  const cssBodySizes = Array.from(
    stage.querySelectorAll('.card-sub, .headline-sub, .checklist-item, .stat-label, .brand-tag, .phone-header-label')
  ).map((el) => parseFloat(window.getComputedStyle(el).fontSize));

  return {
    fillRatio,
    height,
    minL,
    maxR,
    minT,
    maxB,
    stackCenterY,
    maxCxDiff: Math.max(0, ...cxDiffs),
    centerYDiff: Math.abs(stackCenterY - stageCenterY),
    phoneWidth: phoneRect ? phoneRect.width : null,
    phoneHeight: phoneRect ? phoneRect.height : null,
    phoneChildCount,
    zoom,
    headlineSizes,
    heroHeadlineSizes,
    titleSizes,
    ctaSizes,
    phoneHeaderSizes,
    eyebrowSizes,
    bodySizes,
    kineticSizes,
    iconBadgeRects,
    cssHeadlineSizes,
    cssBodySizes,
  };
})()
"""


def _assert_effective_font_and_layout_floors(m: dict, label: str, *, enforce_font_floors: bool = True) -> None:
    assert abs(m["stackCenterY"] - 700.0) <= 60.0, (
        f"{label} stackCenterY={m['stackCenterY']:.1f} outside 700 +- 60"
    )
    assert m["centerYDiff"] <= 28.0, f"{label} off-center vertically: {m['centerYDiff']:.2f}px"
    assert m["maxCxDiff"] <= 4.0, f"{label} off-center horizontally: {m['maxCxDiff']:.2f}px"
    assert m["minL"] >= STAGE_LEFT - 1.0, f"{label} minL={m['minL']:.1f} < {STAGE_LEFT}"
    assert m["maxR"] <= STAGE_RIGHT + 1.0, f"{label} maxR={m['maxR']:.1f} > {STAGE_RIGHT}"
    assert m["minT"] >= STAGE_TOP - 1.0, f"{label} minT={m['minT']:.1f} < {STAGE_TOP}"
    assert m["maxB"] <= STAGE_BOTTOM + 1.0, f"{label} maxB={m['maxB']:.1f} > {STAGE_BOTTOM}"

    if m["phoneWidth"] is not None:
        assert 800.0 <= m["phoneWidth"] <= 905.0, (
            f"{label} phoneWidth={m['phoneWidth']:.1f}px not in [800, 905]"
        )
    if m["phoneHeight"] is not None and m["phoneChildCount"] is not None and m["phoneChildCount"] <= 2:
        assert m["phoneHeight"] >= 600.0, (
            f"{label} sparse phone (children={m['phoneChildCount']}) height={m['phoneHeight']:.1f}px < 600px"
        )
    for badge in m["iconBadgeRects"]:
        assert 140.0 <= badge["width"] <= 200.0, (
            f"{label} IconBadge width={badge['width']:.1f}px not in [140, 200]"
        )
        assert 140.0 <= badge["height"] <= 200.0, (
            f"{label} IconBadge height={badge['height']:.1f}px not in [140, 200]"
        )

    if enforce_font_floors:
        for hs in m["headlineSizes"]:
            assert hs >= 76.0 - 0.5, f"{label} effective headline font-size {hs:.1f}px < 76px"
        for hhs in m["heroHeadlineSizes"]:
            assert hhs >= 88.0 - 0.5, f"{label} effective hero headline font-size {hhs:.1f}px < 88px"
        for ts in m["titleSizes"]:
            assert ts >= 44.0 - 0.5, f"{label} effective card title font-size {ts:.1f}px < 44px"
        for cs in m["ctaSizes"]:
            assert cs >= 44.0 - 0.5, f"{label} effective CTA font-size {cs:.1f}px < 44px"
        for phs in m["phoneHeaderSizes"]:
            assert phs >= 38.0 - 0.5, f"{label} effective phone-header-label font-size {phs:.1f}px < 38px"
        for es in m["eyebrowSizes"]:
            assert es >= 28.0 - 0.5, f"{label} effective eyebrow font-size {es:.1f}px < 28px"
        for bs in m["bodySizes"]:
            assert bs >= 36.0 - 0.5, f"{label} effective body/pill/badge font-size {bs:.1f}px < 36px"
        for ks in m["kineticSizes"]:
            assert ks >= 96.0 - 0.5, f"{label} effective kinetic row font-size {ks:.1f}px < 96px"


@requires_chrome
def test_auto_fit_fill_ratio_for_non_kinetic_styles_and_many_primitive_spec(tmp_path: Path) -> None:
    brand = _sample_malwarebytes_brand()
    scene_text = (
        "Before you answer a call or click a link, Malwarebytes Scam Guard checks if it is a scam. "
        "Download Malwarebytes today."
    )
    words = [
        {"word": "Before", "start": 0.1, "end": 0.35},
        {"word": "answer", "start": 0.6, "end": 0.9},
        {"word": "Scam", "start": 1.8, "end": 2.2},
        {"word": "Download", "start": 3.5, "end": 4.0},
    ]
    non_kinetic_styles = ["flat-explainer", "neo-brutal", "glass-ui", "editorial-clinical"]

    for style_name in non_kinetic_styles:
        html = generate_scene_html(
            scene_text=scene_text,
            style=style_name,
            brand=brand,
            duration_s=5.0,
            words=words,
        )
        m = _evaluate_in_chrome(html, tmp_path, _STACK_METRICS_JS, name=f"fill_tm_{style_name}.html")
        assert 0.78 <= m["fillRatio"] <= 0.92, (
            f"{style_name} test-mode fillRatio={m['fillRatio']:.3f} outside [0.78, 0.92]"
        )
        _assert_effective_font_and_layout_floors(m, f"test-mode/{style_name}", enforce_font_floors=True)

    # Sparse phone (<=2 children) test across non-kinetic styles: phone height >= 600, hero headline >= 88, pill >= 36, icon badge >= 140
    for style_name in non_kinetic_styles:
        sparse_spec = {
            "style": style_name,
            "layout_slot": "phone-hero",
            "gap": "md",
            "bg_variant": "radial",
            "primitives": [
                {"type": "BrandHeader", "props": {"align": "center", "logo_plate": "auto"}},
                {
                    "type": "PhoneFrame",
                    "props": {
                        "header_label": "Scam Guard",
                        "children": [
                            {"type": "PillBadge", "props": {"label": "Incoming Call", "icon": "phone-call", "tone": "accent"}},
                            {"type": "HeadlineBlock", "props": {"headline": "Is it a scam?", "subhead": "Wait before you answer.", "size": "lg"}},
                        ],
                    },
                },
            ],
        }
        html_sparse = render_scene_spec_html(sparse_spec, style=style_name, brand=brand, duration_s=5.0)
        fname_sparse = f"fill_sparse_{style_name}.html"
        m_sparse = _evaluate_in_chrome(html_sparse, tmp_path, _STACK_METRICS_JS, name=fname_sparse)
        assert 0.78 <= m_sparse["fillRatio"] <= 0.92, (
            f"{style_name} sparse fillRatio={m_sparse['fillRatio']:.3f} outside [0.78, 0.92]"
        )
        assert m_sparse["phoneHeight"] is not None and m_sparse["phoneHeight"] >= 600.0
        _assert_effective_font_and_layout_floors(m_sparse, f"sparse/{style_name}", enforce_font_floors=True)

    # Many-primitive spec: 5 stage primitives + 3 phone children + 3 checklist items must scale DOWN <= 0.92 and stay in bounds
    many_spec = {
        "style": "flat-explainer",
        "layout_slot": "phone-hero",
        "gap": "lg",
        "bg_variant": "radial",
        "primitives": [
            {"type": "BrandHeader", "props": {"align": "center", "logo_plate": "auto", "tag_text": "SECURITY"}},
            {
                "type": "HeadlineBlock",
                "props": {
                    "eyebrow": "REAL-TIME AI",
                    "headline": "Complete Mobile Protection",
                    "subhead": "Blocks scam calls and phishing links automatically.",
                    "size": "hero",
                },
            },
            {
                "type": "PhoneFrame",
                "props": {
                    "header_label": "Incoming Call",
                    "children": [
                        {"type": "PillBadge", "props": {"label": "Suspicious Caller", "icon": "alert", "tone": "accent2"}},
                        {
                            "type": "FeatureCard",
                            "props": {
                                "title": "AI Call Screening",
                                "body": "Analyzes caller behavior before your phone rings.",
                                "icon": "shield",
                                "tone": "surface",
                            },
                        },
                        {
                            "type": "StatCallout",
                            "props": {
                                "value": "99.8%",
                                "label": "THREATS BLOCKED",
                                "delta": "+32% FASTER",
                                "meter": "bar",
                                "progress": 0.95,
                            },
                        },
                    ],
                },
            },
            {
                "type": "ChecklistStack",
                "props": {
                    "items": [
                        {"label": "Instant caller ID verification", "state": "check"},
                        {"label": "Zero-day SMS link scanning", "state": "check"},
                        {"label": "Automatic spam quarantine", "state": "check"},
                    ]
                },
            },
            {"type": "CtaButton", "props": {"label": "Enable Scam Guard", "icon": "check", "tone": "accent"}},
        ],
    }
    for style_name in non_kinetic_styles:
        spec_copy = dict(many_spec, style=style_name)
        html_many = render_scene_spec_html(spec_copy, style=style_name, brand=brand, duration_s=5.0)
        fname = f"fill_many_{style_name}.html"
        m_many = _evaluate_in_chrome(html_many, tmp_path, _STACK_METRICS_JS, name=fname)
        assert 0.78 <= m_many["fillRatio"] <= 0.92, (
            f"{style_name} many-primitive fillRatio={m_many['fillRatio']:.3f} outside [0.78, 0.92]"
        )
        _assert_effective_font_and_layout_floors(m_many, f"many/{style_name}", enforce_font_floors=False)
        audit = audit_layout(tmp_path / fname, duration_s=5.0)
        assert audit.passed, f"{style_name} many-primitive failed audit_layout: {audit.violations}"


@requires_chrome
def test_saved_real3_specs_fill_ratio_and_centering(tmp_path: Path) -> None:
    real3_dir = Path("/workspace/francisco-recordings/motion-styles-v1/real3")
    if not real3_dir.is_dir():
        pytest.skip("Saved real3 specs directory not present on this machine.")
    spec_files = sorted(real3_dir.glob("*.spec.json"))
    if not spec_files:
        pytest.skip("No *.spec.json files found in real3 directory.")

    brand = _sample_malwarebytes_brand()
    for sp_path in spec_files:
        spec = json.loads(sp_path.read_text(encoding="utf-8"))
        style_name = str(spec.get("style") or "flat-explainer")
        html = render_scene_spec_html(spec, style=style_name, brand=brand, duration_s=5.0)
        fname = f"real3_{sp_path.stem}.html"
        m = _evaluate_in_chrome(html, tmp_path, _STACK_METRICS_JS, name=fname)
        assert 0.78 <= m["fillRatio"] <= 0.92, (
            f"{sp_path.name} ({style_name}) fillRatio={m['fillRatio']:.3f} outside [0.78, 0.92]"
        )
        _assert_effective_font_and_layout_floors(m, f"real3/{sp_path.name}", enforce_font_floors=True)
        audit = audit_layout(tmp_path / fname, duration_s=5.0)
        assert audit.passed, f"{sp_path.name} failed audit_layout: {audit.violations}"


# ---------------------------------------------------------------------------
# 9. CtaButton guard (never empty or icon-only) & IconBadge never stretches full-width
# ---------------------------------------------------------------------------


@requires_chrome
def test_cta_button_never_empty_or_icon_only_and_icon_badge_not_stretched(tmp_path: Path) -> None:
    brand = _sample_malwarebytes_brand()

    # 1. Spec validation fills empty, whitespace, symbol-only, or too-short CtaButton labels
    for bad_label in ("", "   ", "->", "OK", None):
        raw_spec = {
            "style": "glass-ui",
            "layout_slot": "phone-hero",
            "gap": "md",
            "bg_variant": "orb-glow",
            "primitives": [
                {"type": "BrandHeader", "props": {"logo_plate": "auto"}},
                {
                    "type": "CtaButton",
                    "props": {"label": bad_label, "icon": "phone-call", "tone": "accent"},
                },
            ],
        }
        cleaned_with_copy = validate_scene_spec(
            raw_spec,
            style="glass-ui",
            scene_text="Stop scammers fast. Download Malwarebytes today.",
        )
        cta_label_copy = cleaned_with_copy["primitives"][1]["props"]["label"]
        assert len(cta_label_copy.strip()) >= 3
        assert "Download Malwarebytes" in cta_label_copy or "Learn More" in cta_label_copy

        cleaned_default = validate_scene_spec(raw_spec, style="glass-ui", scene_text="")
        cta_label_default = cleaned_default["primitives"][1]["props"]["label"]
        assert cta_label_default == "Learn More"

    # 2. Rendered styles & specs with both IconBadge and empty CtaButton inside PhoneFrame
    for style_name in CANONICAL_STYLE_ORDER:
        spec = {
            "style": style_name,
            "layout_slot": "phone-hero",
            "gap": "md",
            "bg_variant": "solid",
            "primitives": [
                {"type": "BrandHeader", "props": {"logo_plate": "auto"}},
                {
                    "type": "PhoneFrame",
                    "props": {
                        "header_label": "Scam Guard",
                        "children": [
                            {"type": "IconBadge", "props": {"name": "phone-call", "size": "lg", "tone": "accent"}},
                            {"type": "HeadlineBlock", "props": {"headline": "Scam Likely", "size": "md"}},
                            {"type": "CtaButton", "props": {"label": "   ", "icon": "phone-call", "tone": "accent"}},
                        ],
                    },
                },
            ],
        }
        html = render_scene_spec_html(spec, style=style_name, brand=brand, duration_s=4.0)
        res = _evaluate_in_chrome(
            html,
            tmp_path,
            """
            (() => {
              window.__seek(4.0);
              const ctas = Array.from(document.querySelectorAll('.cta-button')).map((btn) => {
                const lbl = btn.querySelector('.cta-label');
                const st = lbl ? window.getComputedStyle(lbl) : null;
                const r = lbl ? lbl.getBoundingClientRect() : null;
                return {
                  text: lbl ? (lbl.textContent || '').trim() : '',
                  visible: st ? (st.display !== 'none' && st.visibility !== 'hidden' && parseFloat(st.opacity) > 0.9) : false,
                  width: r ? r.width : 0,
                  height: r ? r.height : 0,
                };
              });
              const badges = Array.from(document.querySelectorAll('.icon-badge')).map((b) => {
                const r = b.getBoundingClientRect();
                return { width: r.width, height: r.height };
              });
              return { ctas, badges };
            })()
            """,
            name=f"cta_guard_{style_name}.html",
        )
        assert len(res["ctas"]) == 1
        cta = res["ctas"][0]
        assert len(cta["text"]) >= 3, f"{style_name} CtaButton has empty/short label: {cta}"
        assert cta["visible"] is True
        assert cta["width"] > 40.0 and cta["height"] > 20.0

        assert len(res["badges"]) == 1
        badge = res["badges"][0]
        assert badge["width"] <= 200.0, (
            f"{style_name} IconBadge stretched across phone width ({badge['width']:.1f}px) looking like an icon-only button!"
        )
        assert abs(badge["width"] - badge["height"]) <= 8.0


# ---------------------------------------------------------------------------
# 10. No clipped text (scrollWidth <= clientWidth + 1) & synthetic clipped fixture detection
# ---------------------------------------------------------------------------


@requires_chrome
def test_no_clipped_text_and_audit_detector_catches_synthetic_clipped_fixture(tmp_path: Path) -> None:
    brand = _sample_malwarebytes_brand()

    # Check glass-ui-s2 spec specifically ('Scam Likel' -> 'Scam Likely', no scrollWidth > clientWidth + 1)
    glass_s2_spec = {
        "style": "glass-ui",
        "layout_slot": "phone-hero",
        "gap": "md",
        "bg_variant": "orb-glow",
        "primitives": [
            {"type": "BrandHeader", "props": {"align": "center", "logo_plate": "auto", "tag_text": "Scam Guard"}},
            {
                "type": "PhoneFrame",
                "props": {
                    "header_label": "Scam Guard",
                    "children": [
                        {"type": "IconBadge", "props": {"name": "phone-call", "size": "md", "tone": "accent"}},
                        {
                            "type": "StatCallout",
                            "props": {
                                "value": "Scam Likel",
                                "label": "Incoming Call Checked",
                                "delta": "Suspicious",
                                "meter": "arc",
                            },
                        },
                        {
                            "type": "FeatureCard",
                            "props": {
                                "title": "Link Verified",
                                "body": "Safe to click",
                                "icon": "link",
                                "tone": "surface",
                            },
                        },
                    ],
                },
            },
        ],
    }
    html_s2 = render_scene_spec_html(glass_s2_spec, style="glass-ui", brand=brand, duration_s=4.0)
    assert "Scam Likely" in html_s2
    assert "Scam Likel<" not in html_s2

    for style_name in CANONICAL_STYLE_ORDER:
        html = generate_scene_html(
            "Before you answer a call or click a link, Malwarebytes Scam Guard checks if it is a scam.",
            style=style_name,
            brand=brand,
            duration_s=4.0,
        )
        clipped = _evaluate_in_chrome(
            html,
            tmp_path,
            """
            (() => {
              window.__seek(4.0);
              const bad = [];
              const ignored = new Set(['SCRIPT', 'STYLE', 'TITLE', 'HEAD', 'META', 'LINK', 'DEFS', 'DESC']);
              for (const el of document.querySelectorAll('#safe-stage *')) {
                if (el instanceof SVGElement) continue;
                if (ignored.has(el.tagName.toUpperCase())) continue;
                let hasDirectText = false;
                for (const n of el.childNodes) {
                  if (n.nodeType === 3 && n.nodeValue && n.nodeValue.trim().length > 0) {
                    hasDirectText = true;
                    break;
                  }
                }
                if (!hasDirectText) continue;
                if (el.clientWidth > 0 && el.scrollWidth > el.clientWidth + 1) {
                  bad.push({
                    cls: el.className,
                    text: (el.textContent || '').trim(),
                    scrollWidth: el.scrollWidth,
                    clientWidth: el.clientWidth,
                  });
                }
              }
              return bad;
            })()
            """,
            name=f"noclip_{style_name}.html",
        )
        assert clipped == [], f"{style_name} has clipped text elements: {clipped}"

    # Synthetic clipped fixture: 1) scrollWidth > clientWidth, and 2) clipped by overflow:hidden parent
    synthetic_clipped_html = """<!DOCTYPE html>
<html>
<head>
<style>
  html, body { margin: 0; padding: 0; width: 1080px; height: 1920px; background: #111; color: #fff; font-family: sans-serif; }
  .clip-box { position: absolute; left: 200px; top: 400px; width: 120px; height: 60px; overflow: hidden; }
  .clip-inner { width: 420px; font-size: 44px; white-space: nowrap; }
  .direct-clip { position: absolute; left: 200px; top: 600px; width: 100px; overflow: hidden; white-space: nowrap; font-size: 42px; }
</style>
</head>
<body>
  <div class="clip-box"><div class="clip-inner">This headline is clipped by its overflow hidden parent</div></div>
  <div class="direct-clip">DirectScrollWidthOverflowText</div>
  <script>window.__seek = function() {};</script>
</body>
</html>"""
    syn_path = tmp_path / "synthetic_clipped.html"
    syn_path.write_text(synthetic_clipped_html, encoding="utf-8")
    syn_audit = audit_layout(syn_path, duration_s=2.0, sample_fractions=(0.5,))
    assert not syn_audit.passed, "Expected audit_layout to fail on synthetic clipped fixture"
    violation_types = {v["type"] for v in syn_audit.violations}
    assert "text_clipped" in violation_types or "text_clipped_by_container" in violation_types


# ---------------------------------------------------------------------------
# 11. StatCallout never renders an empty ring/arc
# ---------------------------------------------------------------------------


@requires_chrome
def test_stat_callout_never_renders_empty_ring_and_centers_numeric_arc_value(tmp_path: Path) -> None:
    brand = _sample_malwarebytes_brand()

    # Case A: StatCallout with meter='arc' but non-numeric value ('Scam Likely') or no progress -> ring omitted
    spec_non_numeric_arc = {
        "style": "glass-ui",
        "layout_slot": "phone-hero",
        "gap": "md",
        "bg_variant": "orb-glow",
        "primitives": [
            {"type": "BrandHeader", "props": {"logo_plate": "auto"}},
            {
                "type": "StatCallout",
                "props": {
                    "value": "Scam Likely",
                    "label": "Incoming Call Checked",
                    "delta": "Suspicious",
                    "meter": "arc",
                },
            },
        ],
    }
    html_no_ring = render_scene_spec_html(spec_non_numeric_arc, style="glass-ui", brand=brand, duration_s=3.0)
    assert 'class="stat-arc-svg"' not in html_no_ring
    assert 'class="stat-arc-wrap"' not in html_no_ring

    # Case B: StatCallout with meter='arc', numeric value ('99%'), and progress=0.85 -> ring rendered with centered value
    spec_numeric_arc = {
        "style": "glass-ui",
        "layout_slot": "phone-hero",
        "gap": "md",
        "bg_variant": "orb-glow",
        "primitives": [
            {"type": "BrandHeader", "props": {"logo_plate": "auto"}},
            {
                "type": "StatCallout",
                "props": {
                    "value": "99%",
                    "label": "Scam Calls Blocked",
                    "delta": "+24%",
                    "meter": "arc",
                    "progress": 0.85,
                },
            },
        ],
    }
    html_with_ring = render_scene_spec_html(spec_numeric_arc, style="glass-ui", brand=brand, duration_s=3.0)
    arc_check = _evaluate_in_chrome(
        html_with_ring,
        tmp_path,
        """
        (() => {
          window.__seek(3.0);
          const wrap = document.querySelector('.stat-arc-wrap');
          const svg = wrap ? wrap.querySelector('.stat-arc-svg') : null;
          const val = wrap ? wrap.querySelector('.stat-arc-value') : null;
          const prog = svg ? svg.querySelector('.stat-arc-progress') : null;
          if (!wrap || !svg || !val || !prog) return { ok: false };
          const wr = wrap.getBoundingClientRect();
          const vr = val.getBoundingClientRect();
          const st = window.getComputedStyle(prog);
          return {
            ok: true,
            text: (val.textContent || '').trim(),
            centerXDiff: Math.abs(((wr.left + wr.right) / 2) - ((vr.left + vr.right) / 2)),
            centerYDiff: Math.abs(((wr.top + wr.bottom) / 2) - ((vr.top + vr.bottom) / 2)),
            dashArray: prog.getAttribute('stroke-dasharray') || st.strokeDasharray,
          };
        })()
        """,
        name="stat_numeric_arc.html",
    )
    assert arc_check["ok"] is True
    assert arc_check["text"] == "99%"
    assert arc_check["centerXDiff"] <= 3.0
    assert arc_check["centerYDiff"] <= 3.0


# ---------------------------------------------------------------------------
# 12. Minimum padding (~1.5x preset tokens, >= ~1.4x old floors), gaps, and text-to-edge inset >= 12px
# ---------------------------------------------------------------------------


@requires_chrome
def test_minimum_padding_gaps_and_text_inset_across_all_five_presets(tmp_path: Path) -> None:
    brand = _sample_malwarebytes_brand()

    # Old values -> ~1.4x floor requirements:
    # .phone-frame: old 22px/28px -> floor >= 32px vertical, >= 40px horizontal
    # .feature-card: old 22px/26px -> floor >= 31px vertical, >= 36px horizontal
    # .cta-button: old 18px/36px -> floor >= 25px vertical, >= 50px horizontal
    # .pill-badge: old 14px/30px -> floor >= 20px vertical, >= 42px horizontal
    # .stat-callout: old 20px/26px -> floor >= 28px vertical, >= 36px horizontal
    # .checklist-stack: old 18px/28px -> floor >= 25px vertical, >= 38px horizontal
    padding_floors = {
        ".phone-frame": (32.0, 40.0),
        ".feature-card": (31.0, 36.0),
        ".cta-button": (25.0, 50.0),
        ".pill-badge": (20.0, 42.0),
        ".stat-callout": (28.0, 36.0),
        ".checklist-stack": (25.0, 38.0),
    }

    for style_name in CANONICAL_STYLE_ORDER:
        all_prims_spec = {
            "style": style_name,
            "layout_slot": "phone-hero",
            "gap": "md",
            "bg_variant": "radial",
            "primitives": [
                {"type": "BrandHeader", "props": {"align": "center", "logo_plate": "auto", "tag_text": "PROTECTED"}},
                {
                    "type": "PhoneFrame",
                    "props": {
                        "header_label": "Scam Guard",
                        "children": [
                            {"type": "PillBadge", "props": {"label": "Call Screened", "icon": "shield", "tone": "accent2"}},
                            {
                                "type": "FeatureCard",
                                "props": {
                                    "title": "Real-Time AI Filter",
                                    "body": "Detects suspicious callers before your phone rings.",
                                    "icon": "check",
                                    "tone": "surface",
                                },
                            },
                            {
                                "type": "StatCallout",
                                "props": {"value": "99.4%", "label": "SCAMS BLOCKED", "delta": "+18%", "meter": "bar"},
                            },
                        ],
                    },
                },
                {
                    "type": "ChecklistStack",
                    "props": {
                        "items": [
                            {"label": "Blocks known robocallers", "state": "check"},
                            {"label": "Flags phishing SMS links", "state": "check"},
                        ]
                    },
                },
                {"type": "CtaButton", "props": {"label": "Protect My Phone", "icon": "shield", "tone": "accent"}},
            ],
        }
        html = render_scene_spec_html(all_prims_spec, style=style_name, brand=brand, duration_s=4.0)
        res = _evaluate_in_chrome(
            html,
            tmp_path,
            """
            (() => {
              window.__seek(4.0);
              const stage = document.getElementById('safe-stage');
              const phoneBody = document.querySelector('.phone-body');
              const stageGap = parseFloat(window.getComputedStyle(stage).rowGap || window.getComputedStyle(stage).gap || '0');
              const phoneGap = phoneBody ? parseFloat(window.getComputedStyle(phoneBody).rowGap || window.getComputedStyle(phoneBody).gap || '0') : 0;

              const selectors = ['.phone-frame', '.feature-card', '.cta-button', '.pill-badge', '.stat-callout', '.checklist-stack'];
              const paddings = {};
              for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el) {
                  const st = window.getComputedStyle(el);
                  paddings[sel] = {
                    pt: parseFloat(st.paddingTop),
                    pb: parseFloat(st.paddingBottom),
                    pl: parseFloat(st.paddingLeft),
                    pr: parseFloat(st.paddingRight),
                  };
                }
              }

              const insets = [];
              const containerSels = ['.feature-card', '.cta-button', '.pill-badge', '.stat-callout', '.checklist-stack'];
              const textSels = '.card-title, .card-sub, .cta-label, .pill-label, .stat-value, .stat-label, .checklist-label';
              for (const cSel of containerSels) {
                for (const cEl of document.querySelectorAll(cSel)) {
                  const cr = cEl.getBoundingClientRect();
                  for (const tEl of cEl.querySelectorAll(textSels)) {
                    const tr = tEl.getBoundingClientRect();
                    if (tr.width <= 0 || tr.height <= 0) continue;
                    insets.push({
                      container: cSel,
                      textCls: tEl.className,
                      left: tr.left - cr.left,
                      right: cr.right - tr.right,
                      top: tr.top - cr.top,
                      bottom: cr.bottom - tr.bottom,
                    });
                  }
                }
              }
              return { stageGap, phoneGap, paddings, insets };
            })()
            """,
            name=f"padding_{style_name}.html",
        )

        assert res["stageGap"] >= 28.0, f"{style_name} stage gap {res['stageGap']}px < 28px floor"
        assert res["phoneGap"] >= 20.0, f"{style_name} phone-body gap {res['phoneGap']}px < 20px floor"

        for sel, (min_v, min_h) in padding_floors.items():
            assert sel in res["paddings"], f"{style_name} missing {sel}"
            p = res["paddings"][sel]
            assert p["pt"] >= min_v and p["pb"] >= min_v, (
                f"{style_name} {sel} vertical padding ({p['pt']}, {p['pb']}) < {min_v}px floor"
            )
            assert p["pl"] >= min_h and p["pr"] >= min_h, (
                f"{style_name} {sel} horizontal padding ({p['pl']}, {p['pr']}) < {min_h}px floor"
            )

        assert len(res["insets"]) >= 5
        for ins in res["insets"]:
            min_side = min(ins["left"], ins["right"], ins["top"], ins["bottom"])
            assert min_side >= 12.0, (
                f"{style_name} text {ins['textCls']!r} inside {ins['container']} touches edge "
                f"(min inset {min_side:.1f}px < 12px): {ins}"
            )



