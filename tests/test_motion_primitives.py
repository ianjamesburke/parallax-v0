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
                        {"type": "StatCallout", "props": {"value": "99.9%", "label": "EXTRACHILD DROPPED"}},
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
