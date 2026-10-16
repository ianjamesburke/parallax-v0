"""Machine-readable workflow map: everything an agent needs to drive Parallax, discoverable from the CLI.

Stages, review boundaries, and human decisions are authored here. Command flags are
read from the live CLI so the guide cannot drift from `--help`.
"""
from __future__ import annotations

import click

from . import __version__

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
            "json_fields": ["status", "package_dir", "files", "gates", "advisories", "variety", "pacing_warnings", "next_steps"],
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
        "review_boundary": "Never pick a choice for the human. Never run paid reference generation (`first_run`) without explicit authorization and budget. Generated approximations are not official assets.",
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
        "next": "produce",
    },
    {
        "id": "produce",
        "goal": "Generate stills, narration, and the video. Outside the storyboard package.",
        "commands": [
            {"command": "models list", "use": "Choose models and read price estimates."},
            {"command": "produce", "use": "Refuses to start while reference gates are open. `--yes` never grants approval or budget.", "pass": ["--folder", "--brief"]},
        ],
        "preconditions": ["human approved the storyboard", "no open reference gates"],
        "human_decision": {"question": "Authorize paid generation and set a budget cap.", "ask_only_if_unresolved": False},
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
            "Prefer --json outputs and read their fields instead of scraping text.",
        ],
        "stages": stages,
    }
