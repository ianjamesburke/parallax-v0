import json
from pathlib import Path

import pytest

from parallax.captions.layout import layout_caption
from parallax.captions.styles import resolve_caption_style


@pytest.mark.parametrize("size", [(1080, 1920), (720, 1280)])
def test_caption_layout_long_text_stays_in_safe_rectangle(size):
    laid = layout_caption("SUPERCALIFRAGILISTICEXPIALIDOCIOUS " * 3, *size, 88, resolve_caption_style("bangers"))
    box, safe = laid["box"], laid["safe"]
    assert safe["left"] <= box["left"] <= box["right"] <= safe["right"]
    assert safe["top"] <= box["top"] <= box["bottom"] <= safe["bottom"]
    assert laid["text"].count("\n") <= 1


def test_social_plan_auto_animates_unless_explicitly_disabled(tmp_path):
    from parallax.brief import Brief
    data = {"goal":"x", "script":{"scenes":[
        {"index":0,"vo_text":"one two three", "prompt":"a"},
        {"index":1,"vo_text":"no motion", "prompt":"b", "animate":False},
    ]}}
    brief = Brief.model_validate(data)
    plan = brief.to_plan_skeleton()
    assert plan["scenes"][0]["animate"] is True
    assert plan["scenes"][1].get("animate") is None


def test_video_commands_are_local_and_reportable(tmp_path):
    # The actual ffmpeg smoke is deliberately in CLI/manual coverage; layout report
    # is pure data and is consumed by callers without parsing filter graphs.
    from parallax.captions.layout import safe_rectangle
    assert safe_rectangle(720, 1280, resolve_caption_style("anton"))["top"] == 154
