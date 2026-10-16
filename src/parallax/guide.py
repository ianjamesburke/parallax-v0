"""Machine-readable workflow map: everything an agent needs to drive Parallax, discoverable from the CLI.

Stages, review boundaries, and human decisions are authored here. Command flags are
read from the live CLI so the guide cannot drift from `--help`.
"""
from __future__ import annotations

import click

from . import __version__
from .reference_gates import DETECTION

_BRIEF = {
    "example": {
        "goal": "30-second ad for a sleep tracker",
        "headline": "Wake up sharp",
        "narration_locked": False,
        "assets": {"provided": [{"path": "references/style.png", "kind": "style_ref", "description": "look and palette"}]},
        "script": {"scenes": [
            {"index": 0, "shot_type": "broll", "scene_type": "still", "vo_text": "You wake up tired.",
             "prompt": "A person slumped at a kitchen table, morning light", "image_refs": ["references/style.png"]},
            {"index": 1, "shot_type": "screen", "scene_type": "still", "vo_text": "Your tracker shows why.",
             "prompt": "The product app on a phone showing a sleep chart", "on_screen_text": "Sleep score\n62 / 100"},
        ]},
    },
    "required": ["goal", "script.scenes[].index", "script.scenes[].vo_text", "script.scenes[].prompt"],
    "scene_fields": {
        "shot_type": {
            "values": ["broll", "character", "screen"],
            "default": "broll",
            "meaning": "How production wires global references. character: a provided character_ref is used as the scene reference. broll: a provided product_ref is added to the scene references. screen: no global wiring.",
        },
        "scene_type": {
            "values": ["still", "animation", "motion_graphic"],
            "default": "still, or animation when animate: true",
            "meaning": "What is produced. still: one generated image. animation: a still then animated (needs motion_prompt). motion_graphic: a locally rendered clip_path, never AI generated.",
        },
        "combinations": "shot_type and scene_type are independent; any pair is valid. scene_type: animation implies animate: true and a motion_prompt.",
        "on_screen_text": "Review copy, not narration. Keep each line at most 18 characters for 9:16; put a newline between lines.",
        "narration_locked": "Top-level brief field. true marks the narration as final: pacing advisories get locked_by_narration: true. They are not resolved; fix them visually (split or vary action inside shots).",
    },
    "vocabulary": {
        "gate": "A blocking decision a human must answer. Listed in storyboard `gates`; blocks `produce`, not review.",
        "advisory": "A non-blocking finding a human weighs. Everything non-blocking is in storyboard `advisories`: per-scene advisories, opening pacing, and visual variety. Each has `kind`, `scene` (null for the whole storyboard), `message`, and `remedies`; pacing items add `locked_by_narration` (the brief declares narration final; the advisory still stands, the fix is visual).",
        "validate": "`parallax validate` calls its non-blocking findings `warnings`. Pacing items use the same identifier: validate `code` equals storyboard advisory `kind` (opening_long_hold, opening_few_cuts, opening_delayed_first_word).",
    },
    "script_mapping": {
        "note": "No command parses a script file; the operator maps it into brief.yaml by this rule.",
        "headline": "A labeled Headline:/H: section becomes top-level `headline`: opening on-screen text, never spoken, never in vo_text.",
        "narration": "Body text becomes scene `vo_text`, split across scenes at word boundaries; the scenes' vo_text joined in order must equal the body text exactly. Do not paraphrase.",
        "direction_only_beats": "A beat with direction but no narration cannot be its own scene. vo_text is required; an empty vo_text passes the schema but alignment gives that scene about 0.05s, and a brief has no duration field. Split a neighboring scene's vo_text at a word boundary to give the beat narration, or pin duration_s by hand in plan.yaml after planning.",
        "direction": "Visual direction is authored into scene `prompt` (and `motion_prompt` for animation). On-screen copy goes in `on_screen_text`.",
    },
    "brief_fields": {
        "character_reference": "Top-level bool, default false. Read only by `brief.to_plan_skeleton`: when true, every shot_type: character scene gets `reference: true` in plan.yaml so still generation uses character_image. The planner already does this whenever a character_ref asset exists, so the flag changes nothing in that case. The reference gate does not read it; the gate reads assets.provided kinds and per-scene image_refs.",
    },
    "reference_gates": {
        "detection": DETECTION,
        "order": "Resolve the product gate before the editorial gate; any image_refs on a scene removes it from the editorial gate. Restructure scenes (splits, merges) before resolving, because gate scene lists follow the brief.",
        "scoping": "`reference resolve` is scene-scoped: it adds the file to image_refs of the named scenes only and records the asset with scene_scope, so it never becomes global wiring. A hand-authored assets.provided entry with no scene_scope is global: a character_ref covers every shot_type: character scene and a product_ref every shot_type: broll scene.",
        "changing_an_answer": "Humans change their minds. Run `reference resolve` again with --scenes naming the answered scenes: the new answer replaces the old one for exactly those scenes (output lists `replaced`), detaching the old file from them. status describes review readiness only; `produce_blocked` and `blocked_by` say whether produce would refuse right now.",
        "declaration": "Every scene image_refs entry must also be declared in assets.provided with a kind. `validate` and `storyboard` error on an undeclared file instead of ignoring it.",
        "review_status": "Provided assets start as review_status: pending. Only a human acceptance sets approved, by editing brief.yaml; the CLI never approves.",
    },
}

_STAGES = [
    {
        "id": "intake",
        "goal": "Read the supplied script, notes, and references. Treat the project folder as the whole input boundary.",
        "commands": [],
        "preconditions": ["A project folder holding the script and any reference files."],
        "human_decision": {
            "question": "Preserve the script verbatim? Storyboard only or the full video? Any format, voice, or spending constraint?",
            "ask_only_if_unresolved": True,
        },
        "spends_money": False,
        "next": "brief",
    },
    {
        "id": "brief",
        "goal": "Author brief.yaml from the script: headline, per-scene narration, visual direction, scene types, reference paths.",
        "commands": [
            {"command": "schema", "use": "`parallax schema brief` prints the brief.yaml JSON Schema; `parallax schema cli` prints the full command tree."},
            {"command": "validate", "use": "Check the brief.", "pass": ["--folder", "--brief"]},
        ],
        "preconditions": ["intake complete"],
        "outputs": ["<folder>/brief.yaml"],
        "review_boundary": "Do not rewrite an approved script. Propose concept_name as a proposal; never invent concept_id.",
        "spends_money": False,
        "next": "storyboard",
    },
    {
        "id": "storyboard",
        "goal": "Write the portable storyboard package and detect open reference gates. No provider calls.",
        "commands": [{
            "command": "storyboard",
            "use": "Read `status`, `gates`, `advisories`, `next_steps` from the JSON.",
            "pass": ["--folder", "--brief", "--json"],
            "json_fields": ["status", "package_dir", "html", "files", "gates", "produce_blocked", "blocked_by", "advisories", "next_steps"],
            "check_only": "Add --check (instead of writing a package) to preview status, gates, and advisories as JSON without writing files.",
            "writes": ["<folder>/storyboard-package/"],
        }],
        "preconditions": ["brief validates"],
        "outputs": ["storyboard-package/handoff.json", "storyboard-package/storyboard.html"],
        "spends_money": False,
        "next": "reference_gates",
    },
    {
        "id": "reference_gates",
        "goal": "Resolve every open gate in `storyboard --json` `gates`, then re-run storyboard until status is ready_for_review.",
        "commands": [{
            "command": "reference resolve",
            "use": "Do not build this yourself: run the `command` of the choice the human picked, after filling its `fill` placeholders.",
            "writes": ["<brief>"],
        }],
        "preconditions": ["storyboard status is needs_reference_decision"],
        "human_decision": {
            "question": "For each gate: ask the gate's `question`, offering only its `choices` (supplied reference, official-reference discovery, authorized generated anchor, intentional no-reference). Each choice states the human decision it needs.",
            "ask_only_if_unresolved": False,
        },
        "review_boundary": "Never pick a choice for the human. `generated_anchor` records an authorization only; run its `then` generation step (paid) only after the human authorized it, then attach the file. The gate stays open, and produce stays blocked, until the file is attached. Generated approximations are not official assets.",
        "spends_money": False,
        "next": "storyboard_review",
    },
    {
        "id": "storyboard_review",
        "goal": "Show storyboard.html to the human and collect approval or changes.",
        "commands": [],
        "preconditions": ["storyboard status is ready_for_review"],
        "human_decision": {
            "question": "Approve the scene order, narration, visual direction, scene types, references, and on-screen copy?",
            "ask_only_if_unresolved": False,
        },
        "review_boundary": "The CLI records proposals, not approval. Advisories (compound directions, unsafe vertical text, pacing, repetition) need a human look, not an automated pass. On edits, update brief.yaml and re-run storyboard.",
        "spends_money": False,
        "next": "plan",
    },
    {
        "id": "plan",
        "goal": "Materialize the approved brief into plan.yaml. No provider calls.",
        "commands": [{"command": "plan", "use": "Materialize plan.yaml.", "pass": ["--folder", "--brief"], "writes": ["<folder>/parallax/scratch/plan.yaml"]}],
        "preconditions": ["human approved the storyboard"],
        "spends_money": False,
        "next": "spend_approval",
    },
    {
        "id": "spend_approval",
        "goal": "Record the human's budget so paid commands may run. Every paid command (produce, image generate, video animate, audio voiceover) refuses without it.",
        "commands": [{
            "command": "approve spend",
            "use": "HUMAN ONLY: it refuses unless stdin and stdout are a TTY and the human re-types the budget, so an agent cannot run it. Hand the human the exact command (a refused paid command prints it, with a JSON payload: `gate: spend-approval`, `estimate_usd`, `approve_command`) and ask them to run it in their own terminal or with the `!` prefix, then re-run the paid command. `--yes` and non-interactive shells grant nothing.",
            "pass": ["--folder", "--budget-usd", "--authorized-by"],
            "writes": ["<folder>/spend-approval.yaml"],
        }],
        "preconditions": ["plan exists", "human knows the estimate (the cost table `produce` prints, or `parallax models list` prices)"],
        "human_decision": {"question": "How many USD in total may this project spend, and who is authorizing it?", "ask_only_if_unresolved": False},
        "review_boundary": "Never run `approve spend` yourself, never invent the budget or the authorizer. Spend is reserved in the same file before each paid run; approving again replaces the budget and keeps the ledger. PARALLAX_TEST_MODE stubs spend nothing and need no approval.",
        "spends_money": False,
        "next": "produce",
    },
    {
        "id": "produce",
        "goal": "Generate stills, narration, and the video. Outside the storyboard package.",
        "commands": [
            {"command": "models list", "use": "Choose models and read price estimates."},
            {"command": "produce", "use": "Refuses to start while reference gates are open (--brief, or --plan: a plan records its source brief) and until `parallax approve spend` recorded a budget covering the cost estimate.", "pass": ["--folder", "--brief"]},
        ],
        "preconditions": ["human approved the storyboard", "no open reference gates", "spend approval recorded for the estimate"],
        "human_decision": {"question": "Authorize paid generation and set a budget cap (recorded in the spend_approval stage).", "ask_only_if_unresolved": False},
        "review_boundary": "Dry run first with PARALLAX_TEST_MODE=1 (stub assets, no network). Lock approved assets before re-running; unlocked scenes regenerate and cost money.",
        "spends_money": True,
        "next": None,
    },
]


def _flags(command: click.Command) -> list[dict]:
    flags = []
    for param in command.params:
        if isinstance(param, click.Option) and param.name in ("help", "version", "verbose"):
            continue
        flags.append({
            "name": "/".join(param.opts) if isinstance(param, click.Option) else param.name.upper(),
            "required": param.required,
            "is_flag": isinstance(param, click.Option) and param.is_flag,
            "help": getattr(param, "help", None),
        })
    return flags


def build_guide(group: click.Group) -> dict:
    """Return the workflow map, with each command's flags read from the live CLI."""
    stages = []
    for stage in _STAGES:
        commands = []
        for entry in stage["commands"]:
            node: click.Command = group
            for part in entry["command"].split():
                if not isinstance(node, click.Group) or part not in node.commands:
                    raise ValueError(f"guide references unknown command `parallax {entry['command']}`; fix src/parallax/guide.py")
                node = node.commands[part]
            commands.append({**entry, "command": f"parallax {entry['command']}", "flags": _flags(node)})
        stages.append({**stage, "commands": commands})
    return {
        "guide_version": 1,
        "cli_version": __version__,
        "invocation": "`parallax` stands for however you run this checkout or install, e.g. `uv run parallax`.",
        "discovery": ["parallax --help", "parallax <command> --help", "parallax schema brief", "parallax schema cli", "parallax guide"],
        "rules": [
            "Every decision that spends money or chooses a creative direction belongs to the human.",
            "Open reference gates block `parallax produce --brief`.",
            "No paid command runs without a human budget in <folder>/spend-approval.yaml (`parallax approve spend`, which only a human at a terminal can run). `--yes` does not exist; a refusal's JSON says what to ask the human.",
            "Prefer --json outputs and read their fields instead of scraping text.",
        ],
        "brief": _BRIEF,
        "stages": stages,
    }
