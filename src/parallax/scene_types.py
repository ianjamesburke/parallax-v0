"""Shared scene intent validation for briefs and production plans."""
from typing import Any


def normalize_scene_type(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    scene_type = data.get("scene_type")
    if scene_type not in ("still", "animation", "motion_graphic"):
        return data
    expected = scene_type == "animation"
    if "animate" in data and data["animate"] != expected:
        raise ValueError(
            f"scene {data.get('index', '?')}: scene_type={scene_type} requires animate={str(expected).lower()}"
        )
    return {**data, "animate": expected}
