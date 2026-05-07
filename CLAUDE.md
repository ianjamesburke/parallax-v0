# Parallax — Claude Instructions

## After installing or shipping CLI changes

A hook in `~/.claude/settings.json` (PostToolUse on Bash) fires after any `uv tool install ... parallax` command and reminds you to check these files for drift:

- `CLAUDE.md` — this file
- `README.md` — user-facing docs
- `/Users/ianburke/narrative-content/CLAUDE.md` — project-level Claude instructions

## Skills that depend on the parallax CLI

These skills in `/Users/ianburke/narrative-content/.claude/skills/` reference parallax commands, flags, or output structure and may need updating when the CLI changes:

- `finalize/SKILL.md` — calls `parallax produce`, reads `output/vN/`, builds `cost.json`; depends on produce output layout and CLI flags

If you add, rename, or change the behavior of any CLI command, flag, or output directory structure, check the skills above before closing the branch.
