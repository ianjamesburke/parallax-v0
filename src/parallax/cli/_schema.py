"""parallax schema — prints brief.yaml and plan.yaml field reference."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def register_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "schema",
        help="Emit JSON Schema for brief.yaml / plan.yaml. Omit target for a human-readable overview of both.",
    )
    p.add_argument(
        "target",
        nargs="?",
        choices=("brief", "plan", "cli"),
        default=None,
        help="Which schema to emit: 'brief'/'plan' for YAML field schemas, 'cli' for full CLI surface JSON. Omit to print a human-readable overview of brief+plan.",
    )
    p.add_argument(
        "--output",
        metavar="FILE",
        default=None,
        help="Write schema to FILE instead of stdout (requires a target).",
    )


def run(args: argparse.Namespace) -> int:
    from ..brief import Brief
    from ..plan import Plan

    output: str | None = getattr(args, "output", None)

    if output is not None and args.target is None:
        print(
            "error: --output requires a target: `parallax schema brief --output f.json` or `parallax schema plan --output f.json`",
            file=sys.stderr,
        )
        return 1

    if args.target == "cli":
        from . import build_parser
        text = json.dumps(_serialize_parser(build_parser()), indent=2)
        if output is not None:
            Path(output).write_text(text)
            print(f"Wrote cli schema to {output}", file=sys.stderr)
        else:
            print(text)
        return 0

    if args.target is not None:
        model = Brief if args.target == "brief" else Plan
        text = json.dumps(model.model_json_schema(), indent=2)
        if output is not None:
            Path(output).write_text(text)
            print(f"Wrote {args.target} schema to {output}", file=sys.stderr)
        else:
            print(text)
        return 0

    # Bare `parallax schema` — human-readable overview of both
    brief_model = Brief
    plan_model = Plan
    print("brief.yaml — human spec (what to make; authored by you or the agent)")
    print("plan.yaml  — engine spec (how to make it; locked assets, model picks, per-scene detail)")
    print()
    _print_schema("brief", brief_model)
    print()
    _print_schema("plan", plan_model)
    return 0


def _print_schema(name: str, model) -> None:
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})
    required = set(schema.get("required", []))
    properties = schema.get("properties", {})

    print(f"# {name}.yaml")
    _print_fields(properties, defs, required, prefix="")


def _print_fields(
    properties: dict[str, Any],
    defs: dict[str, Any],
    required: set[str],
    prefix: str,
) -> None:
    for name, prop in properties.items():
        full_name = f"{prefix}{name}" if prefix else name
        _print_field(full_name, prop, defs, name in required)

        # Recurse into nested object models
        resolved = _resolve_ref(prop, defs)
        if resolved and resolved.get("type") == "object":
            sub_props = resolved.get("properties", {})
            sub_req = set(resolved.get("required", []))
            _print_fields(sub_props, defs, sub_req, prefix=f"{full_name}.")

        # Recurse into array-of-object items
        elif resolved and resolved.get("type") == "array":
            items = resolved.get("items", {})
            item_resolved = _resolve_ref(items, defs)
            if item_resolved and item_resolved.get("type") == "object":
                sub_props = item_resolved.get("properties", {})
                sub_req = set(item_resolved.get("required", []))
                _print_fields(sub_props, defs, sub_req, prefix=f"{full_name}[].")

        # Recurse if the property itself is a $ref to an object
        elif "$ref" in prop and resolved and resolved.get("type") == "object":
            pass  # already handled above via resolved check


def _print_field(name: str, prop: dict, defs: dict, is_required: bool) -> None:
    resolved = _resolve_ref(prop, defs)
    effective = resolved if resolved else prop
    type_str = _type_label(effective, defs)
    default = _default_label(prop)
    req_str = "(required)" if is_required else default
    print(f"  {name:<45} {type_str:<30} {req_str}")


def _resolve_ref(prop: dict, defs: dict) -> dict | None:
    """Follow a $ref one level into $defs. Returns None if not a ref."""
    ref = prop.get("$ref")
    if not ref:
        return None
    # $ref format: "#/$defs/ModelName"
    key = ref.split("/")[-1]
    return defs.get(key)


def _type_label(prop: dict, defs: dict) -> str:
    if "enum" in prop:
        return "enum: " + " | ".join(str(v) for v in prop["enum"])

    typ = prop.get("type")
    if typ == "string":
        return "string"
    if typ == "integer":
        return "int"
    if typ == "number":
        return "float"
    if typ == "boolean":
        return "bool"
    if typ == "null":
        return "null"
    if typ == "object":
        return "object"
    if typ == "array":
        items = prop.get("items", {})
        item_resolved = _resolve_ref(items, defs)
        item = item_resolved if item_resolved else items
        if item.get("type") == "object":
            return "list[object]"
        return f"list[{_type_label(item, defs)}]"

    # anyOf / oneOf — common for nullable fields
    any_of = prop.get("anyOf") or prop.get("oneOf")
    if any_of:
        types = [_type_label(t, defs) for t in any_of if t.get("type") != "null"]
        nullable = any(t.get("type") == "null" for t in any_of)
        base = " | ".join(types) if types else "any"
        return f"{base}?" if nullable else base

    return "any"


def _default_label(original: dict) -> str:
    # Default lives on the original (un-resolved) prop in Pydantic v2 JSON Schema
    if "default" in original:
        val = original["default"]
        if val is None:
            return "[default: null]"
        if isinstance(val, (list, dict)):
            return f"[default: {json.dumps(val)}]"
        return f"[default: {val!r}]"
    return ""


# ---------------------------------------------------------------------------
# CLI surface serialization (argparse → JSON)
# ---------------------------------------------------------------------------

def _serialize_parser(parser: argparse.ArgumentParser) -> dict:
    prog_parts = parser.prog.split()
    name = prog_parts[-1] if prog_parts else parser.prog
    result: dict[str, Any] = {
        "name": name,
        "prog": parser.prog,
        "description": parser.description or "",
        "args": [],
        "commands": [],
    }
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for subparser in action.choices.values():
                result["commands"].append(_serialize_parser(subparser))
        elif isinstance(action, (argparse._HelpAction, argparse._VersionAction)):
            continue
        else:
            result["args"].append(_serialize_action(action))
    return result


def _serialize_action(action: argparse.Action) -> dict:
    if action.type is not None:
        type_name = getattr(action.type, "__name__", None) or str(action.type)
    elif isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction, argparse._StoreConstAction)):
        type_name = "bool"
    elif isinstance(action, argparse._CountAction):
        type_name = "int"
    elif isinstance(action, argparse._AppendAction):
        type_name = "list[str]"
    else:
        type_name = "str"

    default = action.default
    if default is argparse.SUPPRESS:
        default = None

    return {
        "flags": list(action.option_strings) if action.option_strings else [action.dest],
        "dest": action.dest,
        "required": getattr(action, "required", not action.option_strings),
        "default": default,
        "type": type_name,
        "choices": list(action.choices) if action.choices else None,
        "nargs": action.nargs,
        "help": action.help or "",
    }
