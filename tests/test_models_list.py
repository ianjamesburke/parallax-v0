"""`parallax models list` is a compact card per model; `show` carries the detail."""
from __future__ import annotations

import json

from parallax import cli
from parallax.models import IMAGE_MODELS, TTS_MODELS, VIDEO_MODELS

_ALL = {**{("image", a): s for a, s in IMAGE_MODELS.items()},
        **{("video", a): s for a, s in VIDEO_MODELS.items()},
        **{("tts", a): s for a, s in TTS_MODELS.items()}}


def test_list_fits_80_columns_and_omits_descriptions(capsys):
    assert cli.main(["models", "list"]) == 0
    out = capsys.readouterr().out
    too_wide = [line for line in out.splitlines() if len(line) > 80]
    assert not too_wide, too_wide
    for spec in _ALL.values():
        # Descriptions live in `show`; none may leak into the list.
        assert spec.description.strip().splitlines()[0][:40] not in out


def test_list_card_shows_alias_tier_slug_cost_and_capabilities(capsys):
    cli.main(["models", "list", "--kind", "image"])
    out = capsys.readouterr().out
    assert "openai/gpt-image-2.5-flare" in out  # live slug, without the openrouter/ prefix
    assert "$0.00375/image" in out  # sub-cent precision kept, unit shown
    assert "9:16" in out and "refs 16" in out and "864x1536" in out


def test_list_covers_every_model_with_one_slug_row(capsys):
    cli.main(["models", "list"])
    out = capsys.readouterr().out
    for (kind, alias), spec in _ALL.items():
        assert spec.alias in out
        assert spec.model_id.removeprefix("openrouter/") in out


def test_list_json_still_includes_description(capsys):
    cli.main(["models", "list", "--json", "--kind", "image"])
    rows = json.loads(capsys.readouterr().out)["image"]
    draft = next(r for r in rows if r["alias"] == "draft")
    assert draft["description"] and draft["request"] == {"quality": "low"}


def test_show_keeps_full_description(capsys):
    assert cli.main(["models", "show", "draft", "--kind", "image"]) == 0
    out = capsys.readouterr().out
    assert IMAGE_MODELS["draft"].description in out
    assert "request:        quality=low" in out
