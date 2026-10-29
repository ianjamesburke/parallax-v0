"""Machine-readable workflow map: everything an agent needs to drive Parallax, discoverable from the CLI.

Stages, review boundaries, and human decisions are authored here. Command flags are
read from the live CLI so the guide cannot drift from `--help`.
"""
from __future__ import annotations

from typing import Any

from . import __version__
from .guide_topics import TOPICS
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
        "headline": "A labeled Headline:/H: section becomes top-level `headline`: opening on-screen text, never spoken, never in vo_text. It is fully opaque on frame 0 by default; use `headline_fade_in_s` only for an explicit override.",
        "narration": "Body text becomes scene `vo_text`, split across scenes at word boundaries; the scenes' vo_text joined in order must equal the body text exactly. Do not paraphrase.",
        "direction_only_beats": "A beat with direction but no narration cannot be its own scene. vo_text is required; an empty vo_text passes the schema but alignment gives that scene about 0.05s, and a brief has no duration field. Split a neighboring scene's vo_text at a word boundary to give the beat narration, or pin duration_s by hand in plan.yaml after planning.",
        "direction": "Visual direction is authored into scene `prompt` (and `motion_prompt` for animation). On-screen copy goes in `on_screen_text`.",
    },
    "brief_fields": {
        "character_reference": "Top-level bool, default false. Read only by `brief.to_plan_skeleton`: when true, every shot_type: character scene gets `reference: true` in plan.yaml so still generation uses character_image. The planner already does this whenever a character_ref asset exists, so the flag changes nothing in that case. The reference gate does not read it; the gate reads assets.provided kinds and per-scene image_refs.",
    },
    "scene_craft": {
        "one_beat": "A scene is one readable visual beat: one location and action. Offer a split when a scene holds several actions or locations or would hold a single shot beyond about 5s (1-4s is typical for fast social creative). Split narration at word boundaries and never rewrite an approved script silently.",
        "compound_direction": "A diptych, split screen, before/after, collage, or 'does X, then does Y' direction must become separate scene beats or an approved motion_graphic. It raises advisory `compound_direction`. A motion_prompt may describe change over time only once the still has one clear composition.",
        "on_screen_text_layout": "Copy is layout, not prompt prose. Keep every element inside title, caption, and platform-safe margins for 9:16; wrap or shorten long copy (advisory `unsafe_vertical_text`).",
        "style": "Plan `style` and `style_hint` steer voice synthesis only. Put the shared art direction into every executable still prompt.",
        "headline": "The production headline overlay lasts through the first scene and is fully visible from frame 0 by default. Do not ask for a headline or opening title fade-in unless it is explicitly requested.",
        "identity": "Keep an existing project title. Otherwise propose a short `concept_name` as a proposal; accept a user-supplied `concept_id` and never invent one. The storyboard heading and browser tab use them; keep the longer objective in `goal`.",
        "script_from_scratch": "For a concept with no script, propose a concise platform-appropriate version first: a specific visual or action hook in the first 1-2s, the tension or benefit on screen early, a varied beat before attention drops, a clean final CTA beat. Prefer a 15-45s first cut; for longer scripts offer concrete short versions before generating.",
        "reference_provenance": "Record every reference in assets.provided with origin (supplied | generated | official), review_status (pending | approved), and source_url for web sources. `provided` means the file exists as an input even if generated earlier. Branded concepts typically need customer branding (official logos, wordmarks, app icons, UI screenshots, product photos, and brand colors/guidelines). When not already supplied in the project folder, use Google/web search or a verified app-store listing to pull official brand references into `references/official/` and record their `source_url`; generated approximations are not official assets. Keep original sources and the prompt, model, and billed cost of any generated reference.",
        "reference_sets": "To turn a style sample into a consistent character or world, a small reusable set (a clean character sheet plus one environment frame) can be generated within the authorized budget (paid) and shown as pending candidates in the storyboard. Use approved assets as per-scene image_refs instead of repeatedly feeding a broad screenshot with unrelated characters and text.",
        "global_vs_scene_refs": "Global character/product references merge with scene references. For mixed character shots and diagrams prefer explicit per-scene image_refs and no global character or product asset, so unrelated images do not condition graphics.",
        "variety": "Motion graphics are explanatory beats, not a fallback for every line (advisories `motion_graphic_share` above 40% of scenes, `motion_graphic_run` for 3+ in a row). Pacing advisories cover the opening; see `parallax guide voiceover_broll`.",
    },
    "reference_gates": {
        "detection": DETECTION,
        "order": "Resolve the product gate before the editorial gate; any image_refs on a scene removes it from the editorial gate. For branded concepts without supplied brand assets, prefer `--choice official` (Google/web/App Store research saved under `references/official/`) over `no_reference`. Restructure scenes (splits, merges) before resolving, because gate scene lists follow the brief.",
        "scoping": "`reference resolve` is scene-scoped: it adds the file to image_refs of the named scenes only and records the asset with scene_scope, so it never becomes global wiring. A hand-authored assets.provided entry with no scene_scope is global: a character_ref covers every shot_type: character scene and a product_ref every shot_type: broll scene.",
        "changing_an_answer": "Humans change their minds. Run `reference resolve` again with --scenes naming the answered scenes: the new answer replaces the old one for exactly those scenes (output lists `replaced`), detaching the old file from them. status describes review readiness only; `produce_blocked` and `blocked_by` say whether produce would refuse right now.",
        "declaration": "Every scene image_refs entry must also be declared in assets.provided with a kind. `validate` and `storyboard` error on an undeclared file instead of ignoring it.",
        "review_status": "Provided assets start as review_status: pending. Only a human acceptance sets approved, by editing brief.yaml; the CLI never approves.",
    },
}

_STAGES = [
    {
        "id": "intake",
        "redirect": "For a script plus existing clips or footage, use `parallax cut` (read `parallax guide cut`).",
        "goal": "Read the supplied script, notes, and references. Check whether a branded concept already has official customer brand images/guidelines in `references/` or needs Google/web brand discovery.",
        "commands": [],
        "preconditions": ["A project folder holding the script and any reference files."],
        "boundary": "The selected project folder is the whole input: pass it as --folder, never search adjacent projects, archived runs, or other folders for substitute inputs. When the script is for a named brand, app, or product and official brand assets (logo, app icon, UI screenshots, product photos) are missing from `references/`, plan to pull official brand assets from the web/App Store (`references/official/`) via the `product-reference` gate (`--choice official`). Choose the performance mode from the brief (voiceover with B-roll, on-screen presenter, or an explicit mix); see `parallax guide voiceover_broll`.",
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
        "see": ["brief"],
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
            "editorial_standard": "Style A editorial pages are standard. HTML is always written; --pdf is opt-in and needs Chrome/Chromium or `uv pip install playwright && playwright install chromium`. For a finished cut, --from-video VIDEO --scenes SCENES uses ffmpeg cuts and extracted frames; scenes is YAML/JSON with title, optional version, and id/title scene entries (optional narration, on_screen_text, treatment, motion, start, end, frame_at, kicker). Quote IDs such as \"00\". Use --cut-threshold to tune cut detection; after every review round edit scenes and re-run the command recorded in handoff.json.",
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
        "see": ["produce_loop", "asset_locking", "models"],
        "spends_money": False,
        "next": "produce",
    },
    {
        "id": "produce",
        "goal": "Generate stills, narration, and the video. Outside the storyboard package.",
        "commands": [
            {"command": "models list", "use": "Choose models and read price estimates."},
            {"command": "produce", "use": "PAID. Prints the cost estimate first, then proceeds; there is no approval step. Refuses to start while reference gates are open (--brief, or --plan: a plan records its source brief).", "pass": ["--folder", "--brief"]},
        ],
        "preconditions": ["human approved the storyboard", "no open reference gates", "human has seen the printed estimate"],
        "human_decision": {"question": "Is the estimate `parallax produce` prints acceptable for this project?", "ask_only_if_unresolved": False},
        "see": ["produce_loop", "asset_locking", "models", "cost", "motion_graphic", "voiceover_broll", "export", "cut"],
        "review_boundary": "Dry run first with PARALLAX_TEST_MODE=1 (stub assets, no network),; it writes nothing into plan.yaml. Lock approved assets before re-running; unlocked scenes regenerate and cost money.",
        "spends_money": True,
        "next": None,
    },
]


def _flags(command: Any) -> list[dict]:
    # Duck-typed: typer >= 0.27 vendors click, so isinstance against click classes is False.
    flags = []
    for param in command.params:
        is_option = param.param_type_name == "option"
        if is_option and param.name in ("help", "version", "verbose"):
            continue
        flags.append({
            "name": "/".join(param.opts) if is_option else param.name.upper(),
            "required": param.required,
            "is_flag": is_option and param.is_flag,
            "help": getattr(param, "help", None),
        })
    return flags


def _build_stages(group: Any) -> list[dict]:
    stages = []
    for stage in _STAGES:
        commands = []
        for entry in stage["commands"]:
            node = group
            for part in entry["command"].split():
                if part not in getattr(node, "commands", {}):
                    raise ValueError(f"guide references unknown command `parallax {entry['command']}`; fix src/parallax/guide.py")
                node = node.commands[part]
            flags = _flags(node)
            # The command has a convenience default, but agents must pass the
            # brief explicitly so they do not storyboard an unintended file.
            if entry["command"] == "storyboard":
                for flag in flags:
                    if flag["name"].split("/")[0] == "--brief":
                        flag["required"] = True
            commands.append({**entry, "command": f"parallax {entry['command']}", "flags": flags})
        stages.append({**stage, "commands": commands})
    return stages


def topic_names() -> list[str]:
    return [*(s["id"] for s in _STAGES), *TOPICS]


def build_guide(group: Any, topic: str | None = None) -> dict:
    """Return the workflow map, or one topic of it, with each command's flags read from the live CLI."""
    stages = _build_stages(group)
    header = {"guide_version": 1, "cli_version": __version__}
    if topic is not None:
        if topic in TOPICS:
            return {**header, "topic": topic, **TOPICS[topic]}
        stage = next((s for s in stages if s["id"] == topic), None)
        if stage is None:
            raise ValueError(f"unknown guide topic {topic!r}; choose one of: {', '.join(topic_names())}")
        # The brief stage carries the full brief contract (fields, examples, gate rules).
        return {**header, "topic": topic, "stage": stage, **({"brief": _BRIEF} if topic == "brief" else {})}
    return {
        **header,
        "start_here": "For a script plus existing clips or footage, use `parallax cut` (read `parallax guide cut`, then `parallax cut --help`), not intake/brief/produce or `audio voiceover` plus hand assembly.",
        "invocation": "`parallax` stands for however you run this checkout or install, e.g. `uv run parallax`.",
        "discovery": ["parallax --help", "parallax <command> --help", "parallax schema brief", "parallax schema plan", "parallax schema cli", "parallax guide", "parallax guide <topic>"],
        "topics": {
            "usage": "`parallax guide <topic>` prints one slice as JSON. Read a stage topic when you reach that stage; read the others when the task needs them.",
            "stages": [s["id"] for s in _STAGES],
            "reference": list(TOPICS),
        },
        "prerequisites": ["ffmpeg and ffprobe on PATH (produce, even in PARALLAX_TEST_MODE)", "OPENROUTER_API_KEY for paid runs"],
        "rules": [
            "Every decision that spends money or chooses a creative direction belongs to the human.",
            "Open reference gates block `parallax produce --brief`.",
            "Paid commands (produce, image generate, image analyze, video animate, audio voiceover) print their estimated cost before the provider call and then proceed. There is no approval step; the OpenRouter key limit is the user's own backstop.",
            "Prefer --json outputs and read their fields instead of scraping text.",
        ],
        "brief": _BRIEF,
        "stages": stages,
    }
