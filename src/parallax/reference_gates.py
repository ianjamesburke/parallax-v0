"""Structured reference gates: unresolved creative decisions recorded before any paid generation.

A gate names the question, the allowed choices, and the exact CLI command that
resolves each choice. Resolution edits brief.yaml (the authored artifact), so the
decision is visible, diffable, and independent of any hidden state.
"""
from __future__ import annotations

import os
import re
import shlex
import tempfile
from pathlib import Path

from ruamel.yaml import YAML

from .brief import Brief

EDITORIAL = "editorial-reference"
PRODUCT = "product-reference"

_PRODUCT_TERMS = re.compile(r"\b(?:app|product|logo|wordmark|ui|brand)\b", re.IGNORECASE)
_PRODUCT_EXCLUSION = re.compile(
    r"\b(?:no|without|avoid|generic|abstract)\s+(?:legible\s+)?(?:branded?\s+)?(?:app|product|logo|wordmark|ui|brand)\b",
    re.IGNORECASE,
)

_CHOICES = {
    "supplied": ("Use a reference the human supplies.",
                 "Human provides the image file, saved under the project folder.",
                 ["<asset>"]),
    "official": ("Discover official customer brand/product material via Google/web search or verified app-store listing (logos, wordmarks, app icons, UI screenshots, product photos), save under references/official/, and mark pending review.",
                 "Human authorizes web/App Store research to pull official brand images and guidelines.",
                 ["<asset>", "<source-url>"]),
    "generated_anchor": ("Authorize one paid style/character anchor, generate it, then attach the file as a pending generated reference.",
                         "Human explicitly authorizes the paid generation and its budget. Never choose this silently.",
                         ["<authorizer>"]),
    "no_reference": ("Proceed without a reference on purpose.",
                     "Human confirms the scenes should be generated with no reference.",
                     ["<reason>"]),
}
_ALLOWED = {
    EDITORIAL: ["supplied", "official", "generated_anchor", "no_reference"],
    PRODUCT: ["supplied", "official", "no_reference"],
}
_DEFAULT_KIND = {EDITORIAL: "style_ref", PRODUCT: "product_ref"}


def _indexes(scenes: list[int]) -> str:
    return ", ".join(f"{index:02d}" for index in scenes)


def _waived(brief: Brief, gate: str) -> set[int]:
    return {index for d in brief.reference_decisions
            if d.gate == gate and d.choice == "no_reference" for index in d.scenes}


def _authorized(brief: Brief, gate: str) -> dict[int, str]:
    """Scenes whose generated anchor is authorized but not yet attached, with the authorization note."""
    return {index: d.note for d in brief.reference_decisions
            if d.gate == gate and d.choice == "generated_anchor" and d.asset is None for index in d.scenes}


def _kinds(brief: Brief) -> set[str]:
    """Kinds of globally wired assets; scene-scoped assets never cover other scenes."""
    return {a.kind for a in brief.assets.provided if a.scene_scope is None}


_REACH = {"character_ref": "character", "product_ref": "broll"}


def _unreached_notes(brief: Brief, scene_indexes: list[int]) -> dict[str, str]:
    """For scenes a global reference exists for but does not reach, say why and how to fix it."""
    kinds = _kinds(brief)
    notes = {}
    for scene in brief.script.scenes:
        if scene.index not in scene_indexes or scene.image_refs:
            continue
        reasons = [f"a {kind} exists but scene {scene.index} is shot_type: {scene.shot_type}, and a global {kind} only reaches shot_type: {reach} scenes"
                   for kind, reach in _REACH.items() if kind in kinds and scene.shot_type != reach]
        if reasons:
            notes[str(scene.index)] = "; ".join(reasons) + f". Add the file to scene {scene.index} image_refs or change its shot_type."
    return notes


def undeclared_image_refs(brief: Brief) -> list[tuple[int, str]]:
    """Scene image_refs entries missing from assets.provided (or another scene's still_path for continuity)."""
    declared = {a.path for a in brief.assets.provided} | {s.still_path for s in brief.script.scenes if s.still_path}
    return [(scene.index, ref) for scene in brief.script.scenes for ref in scene.image_refs if ref not in declared]


def undeclared_message(scene: int, ref: str) -> str:
    return (f"scene {scene} image_refs entry {ref!r} is not declared in assets.provided. Declare it there with a kind "
            "(style_ref, product_ref or character_ref) so reference gates can see it.")


def _referenced(brief: Brief, scene) -> bool:
    """True when production will feed this scene a reference image.

    Mirrors the wiring: planner sets reference: true on shot_type character scenes when a
    character_ref exists (planner.py), and stage_stills prepends product_image to shot_type
    broll scenes (stages.py).
    """
    kinds = _kinds(brief)
    return bool(scene.image_refs
                or (scene.shot_type == "character" and "character_ref" in kinds)
                or (scene.shot_type == "broll" and "product_ref" in kinds))


def _arg(value: str) -> str:
    """Shell-quote a path; leave <placeholders> readable."""
    return value if value.startswith("<") and value.endswith(">") else shlex.quote(value)


def _command(folder: str, brief_path: str, gate: str, choice: str, scenes: list[int], flags: str) -> str:
    return (f"parallax reference resolve --folder {_arg(folder)} --brief {_arg(brief_path)} "
            f"--gate {gate} --choice {choice} {flags} --scenes {','.join(map(str, scenes))}")


_FLAGS = {
    "supplied": "--asset <asset>",
    "official": "--asset <asset> --source-url <source-url>",
    "generated_anchor": "--authorized-by <authorizer>",
    "no_reference": "--reason <reason>",
}
DETECTION = {
    EDITORIAL: {
        "reads": ["script.scenes[].scene_type", "script.scenes[].image_refs",
                  "script.scenes[].shot_type + assets.provided[].kind",
                  "reference_decisions[]"],
        "rule": "A scene is listed when its resolved scene type is still or animation (not motion_graphic), it has no image_refs, "
                "it is not covered by global wiring, and no no_reference decision covers it. Global wiring that counts: a provided character_ref covers shot_type: character scenes; "
                "a provided product_ref covers shot_type: broll scenes. A style_ref never covers a scene on its own; attach it per scene with image_refs.",
        "order_note": "Any image_refs on a scene, including a product_ref attached by the product gate, removes that scene from this gate. "
                      "Resolve the product gate first, re-run storyboard, then answer this gate for the scenes still listed. "
                      "Only pass --scenes that appear in the gate you are resolving.",
    },
    PRODUCT: {
        "reads": ["script.scenes[].vo_text", "script.scenes[].prompt", "script.scenes[].on_screen_text", "script.scenes[].image_refs",
                  "script.scenes[].shot_type", "assets.provided[].kind", "reference_decisions[]"],
        "rule": "A scene is listed when its vo_text, prompt, or on_screen_text contains the word app, product, logo, wordmark, ui, or brand "
                "(not preceded by no/without/avoid/generic/abstract), unless its image_refs include an assets.provided entry of kind product_ref, "
                "it is a shot_type: broll scene and any product_ref is provided, or a no_reference decision covers it. Brand names alone do not trigger it.",
        "order_note": "Only a product_ref in image_refs or a no_reference decision removes a scene from this gate; a style_ref does not.",
    },
}


def _gate(brief: Brief, folder: str, brief_path: str, gate_id: str, kind: str, scenes: list[int], question: str) -> dict:
    authorized = {i: note for i, note in _authorized(brief, gate_id).items() if i in scenes}
    pending = set(authorized) == set(scenes)
    choices = []
    for choice in _ALLOWED[gate_id]:
        summary, human, fill = _CHOICES[choice]
        entry = {"id": choice, "summary": summary, "human_decision": human, "fill": fill,
                 "command": _command(folder, brief_path, gate_id, choice, scenes, _FLAGS[choice])}
        if choice == "generated_anchor":
            entry["records"] = "Authorization only. No file is needed and nothing is generated by this command."
            entry["fill"] = ["<authorizer>"]
            entry["then"] = [
                f'parallax image generate "<prompt>" --aspect <aspect> --out <asset>   (paid; prints its estimate first; run only after the authorization above)',
                _command(folder, brief_path, gate_id, choice, scenes, "--asset <asset>") + "   (attaches the generated file)",
            ]
            if pending:
                entry["summary"] = "Anchor authorized, not yet generated. Generate it, then attach the file."
                entry["human_decision"] = "Already authorized: " + "; ".join(sorted(set(authorized.values()))) + ". No further decision needed to attach."
                entry["command"] = _command(folder, brief_path, gate_id, choice, scenes, "--asset <asset>")
                entry["fill"] = ["<asset>"]
                entry["then"] = [f'parallax image generate "<prompt>" --aspect <aspect> --out <asset>   (paid; prints its estimate first; run first)']
        choices.append(entry)
    gate = {
        "id": gate_id,
        "kind": kind,
        "status": "open",
        "state": "authorized_pending_generation" if pending else "open",
        "scenes": scenes,
        "question": question,
        "detection": DETECTION[gate_id],
        "blocks": ["parallax produce --brief"],
        "choices": choices,
    }
    notes = _unreached_notes(brief, scenes)
    if notes:
        gate["scene_notes"] = notes
        gate["question"] += " " + " ".join(f"Scene {i}: {note}" for i, note in notes.items())
    if authorized:
        gate["authorized_scenes"] = {str(i): note for i, note in authorized.items()}
    if gate_id == PRODUCT:
        gate["excluded_choices"] = [{
            "id": "generated_anchor",
            "why": "Generated approximations are not official logos or UI; supply or discover official material, or proceed with explicit placeholders.",
        }]
    return gate


def open_gates(brief: Brief, folder: str | Path, brief_path: str | Path) -> list[dict]:
    """Return unresolved reference gates in the order a human should answer them.

    A gate whose anchor is authorized but not generated stays listed: produce must not run unanchored.
    """
    folder, brief_path = str(folder), str(brief_path)
    scenes = brief.script.scenes
    gates = []
    waived = _waived(brief, EDITORIAL)
    editorial = [s.index for s in scenes
                 if s.resolved_scene_type != "motion_graphic" and not _referenced(brief, s) and s.index not in waived]
    if editorial:
        kinds = sorted(_kinds(brief) & {"style_ref", "character_ref", "product_ref"})
        note = (f"Global reference(s) exist ({', '.join(kinds)}) but do not cover these scenes: a style_ref never covers a scene by itself "
                "(list it in the scene's image_refs), a character_ref covers shot_type: character scenes, and a product_ref covers shot_type: broll scenes."
                if kinds else "No global style or character reference exists.")
        gates.append(_gate(brief, folder, brief_path, EDITORIAL, "unanchored_editorial_scenes", editorial,
                           f"{len(editorial)} editorial still/animation scene(s) have no explicit image reference "
                           f"({_indexes(editorial)}). {note} How should their look be anchored before paid still generation?"))
    waived = _waived(brief, PRODUCT)
    product_paths = {a.path for a in brief.assets.provided if a.kind == "product_ref"}
    global_product = "product_ref" in _kinds(brief)
    product = [s.index for s in scenes
               if not product_paths.intersection(s.image_refs) and s.index not in waived
               and not (s.shot_type == "broll" and global_product)
               and _PRODUCT_TERMS.search(f"{s.vo_text} {s.prompt} {s.on_screen_text}")
               and not _PRODUCT_EXCLUSION.search(f"{s.vo_text} {s.prompt} {s.on_screen_text}")]
    if product:
        gates.append(_gate(brief, folder, brief_path, PRODUCT, "product_scene_missing_official_reference", product,
                           f"Product/brand-dependent scene(s) {_indexes(product)} have no assigned reference. "
                           "Branded concepts typically need customer branding (official logo, wordmark, app icon, UI screenshots, or product photos). "
                           "Where does the official logo, product, or UI material come from? If not already supplied in the folder, do Google/web/App Store research (--choice official) to pull official brand assets into references/official/ rather than defaulting to no_reference."))
    return gates


def resolve_gate(
    brief_path: str | Path, folder: str | Path, gate_id: str, choice: str, *,
    scenes: list[int] | None = None, asset: str | None = None, kind: str | None = None,
    source_url: str | None = None, authorized_by: str | None = None, reason: str | None = None,
) -> dict:
    """Record a human's answer to one gate in brief.yaml and return what changed."""
    brief_path, root = Path(brief_path).expanduser(), Path(folder).expanduser().resolve()
    brief = Brief.from_yaml(brief_path)
    if gate_id not in _ALLOWED:
        raise ValueError(f"{gate_id!r} is not a known gate; known gates: {sorted(_ALLOWED)}")
    gate = next((g for g in open_gates(brief, root, brief_path) if g["id"] == gate_id), None)
    open_scenes = gate["scenes"] if gate else []
    answered = sorted({i for d in brief.reference_decisions if d.gate == gate_id for i in d.scenes})
    if choice not in _ALLOWED[gate_id]:
        raise ValueError(f"choice {choice!r} is not allowed for gate {gate_id}; allowed: {_ALLOWED[gate_id]}")
    if scenes is None and not open_scenes:
        raise ValueError(f"gate {gate_id!r} has no open scenes in {brief_path}. To change an earlier answer, pass --scenes "
                         f"with scene indexes it covers ({answered or 'none answered yet'}).")
    targets = open_scenes if scenes is None else scenes
    stray = sorted(set(targets) - set(open_scenes) - set(answered))
    if stray:
        raise ValueError(f"scenes {stray} are not part of gate {gate_id}; open scenes: {open_scenes}, answered scenes: {answered}")

    authorization_only = choice == "generated_anchor" and not asset
    pending = [d for d in brief.reference_decisions if d.gate == gate_id and d.choice == "generated_anchor"
               and d.asset is None and set(d.scenes) == set(targets)]
    if choice == "no_reference":
        if not reason:
            raise ValueError("--reason is required for --choice no_reference: state why these scenes need no reference")
        note = reason
    else:
        if choice != "generated_anchor" and not asset:
            raise ValueError(f"--asset is required for --choice {choice}: path to the reference file under {root}")
        if asset:
            path = Path(asset).expanduser()
            if path.is_absolute() or not (root / path).resolve().is_relative_to(root):
                raise ValueError(f"--asset must be a relative path inside {root}, got {asset!r}")
            if not (root / path).is_file():
                raise ValueError(f"--asset file not found: {root / path}")
        if choice == "official" and not source_url:
            raise ValueError("--source-url is required for --choice official: the page the material came from")
        if choice == "generated_anchor" and not authorized_by and not (asset and pending):
            raise ValueError("--authorized-by is required for --choice generated_anchor: who authorized the paid generation. "
                             "Record the authorization first; attach the generated file later with --asset")
        if authorization_only and pending:
            raise ValueError(f"scenes {targets} already have an authorized, ungenerated anchor; attach it with --asset")
        note = {"supplied": "supplied by the human", "official": source_url,
                "generated_anchor": f"authorized by {authorized_by}" if authorized_by else pending[0].note if pending else ""}[choice]

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    with brief_path.open(encoding="utf-8") as fp:
        doc = yaml.load(fp)
    completing = choice == "generated_anchor" and bool(asset) and bool(pending)
    replaced = [] if completing else _clear_answers(doc, gate_id, set(targets))
    if choice != "no_reference" and not authorization_only:
        origin = {"supplied": "supplied", "official": "official", "generated_anchor": "generated"}[choice]
        provided = doc.setdefault("assets", {}).setdefault("provided", [])
        existing = next((a for a in provided if a.get("path") == asset), None)
        if existing is None:
            entry = {"path": asset, "kind": kind or _DEFAULT_KIND[gate_id], "origin": origin, "review_status": "pending",
                     "scene_scope": sorted(targets)}
            if source_url:
                entry["source_url"] = source_url
            provided.append(entry)
        elif existing.get("scene_scope") is not None:
            existing["scene_scope"] = sorted(set(existing["scene_scope"]) | set(targets))
        for scene in doc["script"]["scenes"]:
            if scene["index"] in targets and asset not in scene.get("image_refs", []):
                scene.setdefault("image_refs", []).append(asset)
    if choice == "generated_anchor" and asset and pending:
        for decision in doc["reference_decisions"]:
            if (decision["gate"] == gate_id and decision["choice"] == "generated_anchor"
                    and "asset" not in decision and set(decision["scenes"]) == set(targets)):
                decision["asset"] = asset
    else:
        record = {"gate": gate_id, "choice": choice, "scenes": list(targets), "note": note}
        if asset:
            record["asset"] = asset
        doc.setdefault("reference_decisions", []).append(record)

    Brief.model_validate(_plain(doc))  # fail before writing, never leave a broken brief
    fd, tmp = tempfile.mkstemp(dir=brief_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            yaml.dump(doc, fp)
        os.replace(tmp, brief_path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    remaining = open_gates(Brief.from_yaml(brief_path), root, brief_path)
    still_open: dict[int, list[str]] = {}
    for gate in remaining:
        for index in gate["scenes"]:
            still_open.setdefault(index, []).append(gate["id"])
    covered = sorted(index for index in targets if gate_id not in still_open.get(index, []))
    attached = asset if choice != "no_reference" and asset else None
    return {
        "gate": gate_id, "choice": choice,
        "wrote": [str(brief_path)],
        "replaced": replaced,
        "scenes_requested": list(targets),
        "scenes_newly_covered": covered,
        "scenes_still_open": {str(i): ids for i, ids in sorted(still_open.items())},
        "open_gates": [{"id": g["id"], "state": g["state"], "scenes": g["scenes"]} for g in remaining],
        "pending_human_approval": (
            [{"asset": attached, "review_status": "pending",
              "note": "A human accepts it by setting review_status: approved in brief.yaml. The CLI never approves."}] if attached else []),
    }


def _clear_answers(doc, gate_id: str, targets: set[int]) -> list[dict]:
    """Remove the named scenes from earlier decisions of this gate so a new answer replaces them.

    Also detaches the earlier decision's asset from those scenes, and drops a scene-scoped asset that no scene uses any more.
    """
    replaced = []
    decisions = doc.get("reference_decisions") or []
    for decision in list(decisions):
        hit = [i for i in decision["scenes"] if i in targets]
        if decision["gate"] != gate_id or not hit:
            continue
        asset = decision.get("asset")
        replaced.append({"choice": decision["choice"], "scenes": hit, **({"asset": asset} if asset else {})})
        decision["scenes"] = [i for i in decision["scenes"] if i not in targets]
        if asset:
            for scene in doc["script"]["scenes"]:
                if scene["index"] in hit and asset in scene.get("image_refs", []):
                    scene["image_refs"].remove(asset)
                    if not scene["image_refs"]:
                        del scene["image_refs"]
            provided = doc["assets"]["provided"]
            for entry in list(provided):
                if entry["path"] == asset and entry.get("scene_scope") is not None:
                    entry["scene_scope"] = [i for i in entry["scene_scope"] if i not in hit]
                    if not entry["scene_scope"]:
                        provided.remove(entry)
        if not decision["scenes"]:
            decisions.remove(decision)
    if "reference_decisions" in doc and not doc["reference_decisions"]:
        del doc["reference_decisions"]
    return replaced


def _plain(node):
    """Convert ruamel containers to builtins for pydantic validation."""
    if isinstance(node, dict):
        return {str(k): _plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_plain(v) for v in node]
    return node
