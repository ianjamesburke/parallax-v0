# Parallax — Claude Instructions

## Session start

Read the first ~100 lines of `DEV_LOG.md` before doing anything.

## AGENTS.md is the source of truth for CLI behavior

`AGENTS.md` documents how the CLI works — commands, plan YAML schema, model aliases, iteration patterns, environment variables. **Any time you add, change, or remove CLI behavior, update AGENTS.md immediately.** This includes:

- New subcommands or flags
- New plan YAML fields or semantics
- New model aliases in `pricing.py`
- Changes to how `still_path`, `reference_images`, `audio_path`, or `words_path` are resolved
- Changes to pipeline step order or skipping logic
- New environment variables

Do not leave AGENTS.md stale. A fresh agent reading it should be able to operate the CLI correctly without reading source code.

## No one-off scripts

Never write ad-hoc Python scripts to run pipeline steps. Everything must go through the CLI:

- **All pipeline runs:** `parallax produce --folder <path> --plan <plan.yaml>`

If a use case can't be handled by the CLI, add the capability to the CLI first, then use it.

## Plan YAML is the iteration artifact

When working on a video project, the plan YAML (typically in `.parallax/scratch/`) is the single file to edit between versions. Lock approved stills with `still_path`, lock approved audio with `audio_path`/`words_path`. Never bypass the plan to regenerate individual assets ad-hoc.

## Dev log

Add a `DEV_LOG.md` entry for any non-obvious architectural decision, root cause of a real bug, or approach that was tried and abandoned. See `~/.claude/rules/dev-log.md` for format.

## Python tooling

Use `uv run parallax` to invoke the CLI from the repo root. Never activate a venv manually.
