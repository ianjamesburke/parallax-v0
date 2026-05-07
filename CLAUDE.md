# Parallax — Claude Instructions

## After installing or shipping CLI changes

A `PostToolUse` hook in `~/.claude/settings.json` fires after any `uv tool install ... parallax` command and prints a reminder listing the files to check for drift. That list is the single source of truth — edit the hook to add or remove files, not this document.

## Skills that depend on the parallax CLI

Skills in `/Users/ianburke/narrative-content/.claude/skills/` may reference parallax commands, flags, or output structure. If you change CLI behavior, check that directory for skills that need updating before closing the branch.
