from __future__ import annotations

import argparse
import sys


def register_parser(sub: argparse._SubParsersAction) -> None:
    models_p = sub.add_parser(
        "models", help="Browse the model catalog (image / video / tts aliases)."
    )
    models_sub = models_p.add_subparsers(dest="models_command", required=True)

    models_list_p = models_sub.add_parser("list", help="List every alias grouped by kind.")
    models_list_p.add_argument(
        "--kind", choices=("image", "video", "tts"), default=None,
        help="Filter to a single kind.",
    )
    models_list_p.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON instead of a table."
    )

    models_show_p = models_sub.add_parser("show", help="Show capabilities for one alias.")
    models_show_p.add_argument("alias", help="Model alias (e.g. 'mid', 'kling', 'tts-mini').")
    models_show_p.add_argument(
        "--kind", choices=("image", "video", "tts"), default=None,
        help="Disambiguate when an alias exists in multiple kinds.",
    )


def run(args) -> int:
    from .. import models as _models_pkg
    if args.models_command == "list":
        return _print_models_list(_models_pkg, kind=args.kind, as_json=args.json)
    if args.models_command == "show":
        return _print_model_show(_models_pkg, alias=args.alias, kind=args.kind)
    return 1


def _print_models_list(models_pkg, kind: str | None, as_json: bool) -> int:
    import json as _json

    tables = (
        ("image", models_pkg.IMAGE_MODELS),
        ("video", models_pkg.VIDEO_MODELS),
        ("tts", models_pkg.TTS_MODELS),
    )
    if kind is not None:
        tables = tuple((k, t) for k, t in tables if k == kind)

    if as_json:
        out = {}
        for k, table in tables:
            out[k] = [
                {
                    "alias": s.alias,
                    "model_id": s.model_id,
                    "tier": s.tier,
                    "cost": s.cost_usd,
                    "unit": s.cost_unit,
                    "fallback": s.fallback_alias,
                    "aspect_ratios": list(s.aspect_ratios),
                    "max_refs": s.max_refs,
                    "start_frame": s.start_frame,
                    "end_frame": s.end_frame,
                    "inputs": list(s.inputs),
                    "voices": list(s.voices),
                    "native_resolution": s.native_resolution,
                    "description": s.description,
                }
                for s in table.values()
            ]
        print(_json.dumps(out, indent=2))
        return 0

    _DEFAULT_ALIAS: dict[str, str] = {"image": "mid", "video": "draft", "tts": "tts-mini"}
    _HQ_ALIAS: dict[str, str] = {"image": "premium", "video": "mid", "tts": "tts-gemini"}

    for k, table in tables:
        print(f"\n{k.upper()}:")
        print(f"  {'alias':<18} {'tier':<8} {'cost':<10} {'fallback':<14} description")
        print(f"  {'-' * 18} {'-' * 8} {'-' * 10} {'-' * 14} {'-' * 40}")
        for s in table.values():
            cost = f"${s.cost_usd:.3f}/{s.cost_unit}"
            fb = s.fallback_alias or "—"
            markers = ""
            if s.alias == _DEFAULT_ALIAS.get(k):
                markers = " [default]"
            elif s.alias == _HQ_ALIAS.get(k):
                markers = " [hq]"
            print(f"  {s.alias:<18} {s.tier:<8} {cost:<10} {fb:<14} {s.description}{markers}")
    return 0


def _print_model_show(models_pkg, alias: str, kind: str | None) -> int:
    try:
        spec = models_pkg.resolve(alias, kind=kind)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"alias:          {spec.alias}")
    print(f"kind:           {spec.kind}")
    print(f"tier:           {spec.tier}")
    print(f"model_id:       {spec.model_id}")
    print(f"cost:           ${spec.cost_usd:.4f} / {spec.cost_unit}")
    print(f"fallback:       {spec.fallback_alias or '—'}")
    print(f"aspect_ratios:  {', '.join(spec.aspect_ratios) if spec.aspect_ratios else '—'}")
    if spec.kind == "image":
        print(f"max_refs:       {spec.max_refs}")
        print(f"inputs:         {', '.join(spec.inputs) if spec.inputs else '—'}")
    if spec.kind == "video":
        print(f"start_frame:    {spec.start_frame}")
        print(f"end_frame:      {spec.end_frame}")
        if spec.native_resolution:
            print(f"native_res:     {spec.native_resolution} (generates at this resolution; upscaled to output during assembly)")
    if spec.kind == "tts":
        print(f"tts_backend:    {spec.tts_backend}")
        if spec.voices:
            print(f"voices ({len(spec.voices)}):")
            for v in spec.voices:
                print(f"  - {v}")
        if spec.tts_backend == "speech":
            print()
            print("Emotional tags — inline in your voiceover text, passed to Gemini:")
            print("  Use single-word gerund/adjective/adverb form: [whispering], [excitedly],")
            print("  [dramatically], [rapidly], [softly], [cheerfully], [angrily], etc.")
            print('  "[dramatically] Everything changed. [softly] No one knew."')
            print('  "[rapidly] Three. Two. One. [excitedly] Go!"')
            print('  "[whispering] The secret was simple."')
            print()
            print("Usage (plan.yaml):")
            print("  voice_model: tts-gemini")
            print("  voice: Kore")
            print("  voiceover: |")
            print("    [dramatically] The world you knew is gone.")
            print("    [rapidly] You have five seconds to decide.")
            print("    [softly] Choose wisely.")
        elif spec.tts_backend == "chat_audio":
            print()
            print("Note: inline [emotional] tags are stripped before sending to this backend.")
            print("      Use style= or style_hint= for delivery control instead.")
    if spec.description:
        print(f"\n{spec.description}")
    return 0
