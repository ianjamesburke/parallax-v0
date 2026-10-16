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

_PRODUCT_TERMS = re.compile(r"\b(?:app|product|logo|ui|brand)\b", re.IGNORECASE)
_PRODUCT_EXCLUSION = re.compile(
    r"\b(?:no|without|avoid|generic|abstract)\s+(?:legible\s+)?(?:branded?\s+)?(?:app|product|logo|ui|brand)\b",
    re.IGNORECASE,
)

_CHOICES = {
    "supplied": ("Use a reference the human supplies.",
                 "Human provides the image file, saved under the project folder.",
                 ["<asset>"]),
    "official": ("Discover official material (brand site or verified app-store listing), save it, and mark it pending review.",
                 "Human authorizes browsing for official material.",
                 ["<asset>", "<source-url>"]),
    "generated_anchor": ("Generate one compact style/character anchor, then record it as a pending generated reference.",
                         "Human explicitly authorizes the paid generation and its budget. Never choose this silently.",
                         ["<asset>", "<authorizer>"]),
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


def _command(folder: str, brief_path: str, gate: str, choice: str, scenes: list[int]) -> str:
    flags = {
        "supplied": "--asset <asset>",
        "official": "--asset <asset> --source-url <source-url>",
        "generated_anchor": "--asset <asset> --authorized-by <authorizer>",
        "no_reference": "--reason <reason>",
    }[choice]
    return (f"parallax reference resolve --folder {shlex.quote(folder)} --brief {shlex.quote(brief_path)} "
            f"--gate {gate} --choice {choice} {flags} --scenes {','.join(map(str, scenes))}")


def _gate(folder: str, brief_path: str, gate_id: str, kind: str, scenes: list[int], question: str) -> dict:
    choices = []
    for choice in _ALLOWED[gate_id]:
        summary, human, fill = _CHOICES[choice]
        choices.append({
            "id": choice,
            "summary": summary,
            "human_decision": human,
            "fill": fill,
            "command": _command(folder, brief_path, gate_id, choice, scenes),
        })
        if choice == "generated_anchor":
            choices[-1]["first_run"] = 'parallax image generate "<prompt>" --aspect <aspect> --out <asset>'
            choices[-1]["fill"] = ["<prompt>", "<aspect>", *fill]
    gate = {
        "id": gate_id,
        "kind": kind,
        "status": "open",
        "scenes": scenes,
        "question": question,
        "blocks": ["parallax produce --brief"],
        "choices": choices,
    }
    if gate_id == PRODUCT:
        gate["excluded_choices"] = [{
            "id": "generated_anchor",
            "why": "Generated approximations are not official logos or UI; supply or discover official material, or proceed with explicit placeholders.",
        }]
    return gate


def open_gates(brief: Brief, folder: str | Path, brief_path: str | Path) -> list[dict]:
    """Return unresolved reference gates in the order a human should answer them."""
    folder, brief_path = str(folder), str(brief_path)
    scenes = brief.script.scenes
    gates = []
    waived = _waived(brief, EDITORIAL)
    editorial = [s.index for s in scenes
                 if s.resolved_scene_type != "motion_graphic" and not s.image_refs and s.index not in waived]
    if editorial:
        anchors = [a for a in brief.assets.provided if a.kind in {"style_ref", "character_ref"}]
        note = ("A global style/character reference exists, but these scenes do not name a scene reference."
                if anchors else "No global style or character reference exists.")
        gates.append(_gate(folder, brief_path, EDITORIAL, "unanchored_editorial_scenes", editorial,
                           f"{len(editorial)} editorial still/animation scene(s) have no explicit image reference "
                           f"({_indexes(editorial)}). {note} How should their look be anchored before paid still generation?"))
    waived = _waived(brief, PRODUCT)
    product_paths = {a.path for a in brief.assets.provided if a.kind == "product_ref"}
    product = [s.index for s in scenes
               if not product_paths.intersection(s.image_refs) and s.index not in waived
               and _PRODUCT_TERMS.search(f"{s.prompt} {s.on_screen_text}")
               and not _PRODUCT_EXCLUSION.search(f"{s.prompt} {s.on_screen_text}")]
    if product:
        gates.append(_gate(folder, brief_path, PRODUCT, "product_scene_missing_official_reference", product,
                           f"Product/brand-dependent scene(s) {_indexes(product)} have no assigned reference. "
                           "Where does the official logo, product, or UI material come from?"))
    return gates


def resolve_gate(
    brief_path: str | Path, folder: str | Path, gate_id: str, choice: str, *,
    scenes: list[int] | None = None, asset: str | None = None, kind: str | None = None,
    source_url: str | None = None, authorized_by: str | None = None, reason: str | None = None,
) -> dict:
    """Record a human's answer to one gate in brief.yaml and return what changed."""
    brief_path, root = Path(brief_path).expanduser(), Path(folder).expanduser().resolve()
    brief = Brief.from_yaml(brief_path)
    gates = {g["id"]: g for g in open_gates(brief, root, brief_path)}
    if gate_id not in gates:
        raise ValueError(f"gate {gate_id!r} is not open in {brief_path}; open gates: {sorted(gates) or 'none'}")
    gate = gates[gate_id]
    if choice not in _ALLOWED[gate_id]:
        raise ValueError(f"choice {choice!r} is not allowed for gate {gate_id}; allowed: {_ALLOWED[gate_id]}")
    targets = gate["scenes"] if scenes is None else scenes
    stray = sorted(set(targets) - set(gate["scenes"]))
    if stray:
        raise ValueError(f"scenes {stray} are not part of gate {gate_id}; gate scenes: {gate['scenes']}")

    if choice == "no_reference":
        if not reason:
            raise ValueError("--reason is required for --choice no_reference: state why these scenes need no reference")
        note = reason
    else:
        if not asset:
            raise ValueError(f"--asset is required for --choice {choice}: path to the reference file under {root}")
        path = Path(asset).expanduser()
        if path.is_absolute() or not (root / path).resolve().is_relative_to(root):
            raise ValueError(f"--asset must be a relative path inside {root}, got {asset!r}")
        if not (root / path).is_file():
            raise ValueError(f"--asset file not found: {root / path}")
        if choice == "official" and not source_url:
            raise ValueError("--source-url is required for --choice official: the page the material came from")
        if choice == "generated_anchor" and not authorized_by:
            raise ValueError("--authorized-by is required for --choice generated_anchor: who authorized the paid generation")
        note = {"supplied": "supplied by the human", "official": source_url,
                "generated_anchor": f"authorized by {authorized_by}"}[choice]

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    with brief_path.open(encoding="utf-8") as fp:
        doc = yaml.load(fp)
    if choice != "no_reference":
        origin = {"supplied": "supplied", "official": "official", "generated_anchor": "generated"}[choice]
        provided = doc.setdefault("assets", {}).setdefault("provided", [])
        if not any(a.get("path") == asset for a in provided):
            entry = {"path": asset, "kind": kind or _DEFAULT_KIND[gate_id], "origin": origin, "review_status": "pending"}
            if source_url:
                entry["source_url"] = source_url
            provided.append(entry)
        for scene in doc["script"]["scenes"]:
            if scene["index"] in targets:
                scene.setdefault("image_refs", []).append(asset)
    doc.setdefault("reference_decisions", []).append(
        {"gate": gate_id, "choice": choice, "scenes": list(targets), "note": note})

    Brief.model_validate(_plain(doc))  # fail before writing, never leave a broken brief
    fd, tmp = tempfile.mkstemp(dir=brief_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            yaml.dump(doc, fp)
        os.replace(tmp, brief_path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    remaining = [g["id"] for g in open_gates(Brief.from_yaml(brief_path), root, brief_path)]
    return {"gate": gate_id, "choice": choice, "scenes": list(targets), "brief": str(brief_path), "open_gates": remaining}


def _plain(node):
    """Convert ruamel containers to builtins for pydantic validation."""
    if isinstance(node, dict):
        return {str(k): _plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_plain(v) for v in node]
    return node
