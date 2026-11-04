"""Project reference labels shared by checks and storyboard review."""
import re


def summary(brief):
    entries = []
    for ref in brief.references:
        if ref.status != "missing":
            continue
        scenes = [s.scene_id for s in brief.script.scenes if ref.id in s.refs]
        numbers = [re.fullmatch(r"Scene_(\d+)", name, re.I) for name in scenes]
        if scenes and all(numbers):
            label = ("Scene " if len(scenes) == 1 else "Scenes ") + ", ".join(m.group(1) for m in numbers)
        else:
            label = ", ".join(s.replace("_", " ") for s in scenes)
        entries.append(ref.description + (f" ({label})" if label else ""))
    return "References needed: " + ", ".join(entries) if entries else ""
