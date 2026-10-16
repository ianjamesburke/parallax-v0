---
id: "0004"
title: "Install locked dependency versions"
status: done
priority: p2
size: s
created_at: "2026-10-16T03:01:58Z"
completed_at: "2026-10-16T03:37:45Z"
blocked_by: []
gh_issue: []
area:
  - "cli"
tags:
  - "install"
---

## Why

`just install` runs `uv tool install --reinstall .`, which ignores `uv.lock`, so users get untested dependency versions. v0.5.44 shipped with `parallax guide` crashing because typer 0.27 / click 8.5 differ from the locked 0.25.1 / 8.3.3 (fixed in #193). Tests and users run different dependency sets.

## Done When

- `just install` and `install.sh` install the locked versions (e.g. `uv export --frozen --no-hashes` constraints) or document why not.
- `typer>=0.12,<1` major-version guard in pyproject.
- `tests/test_unlocked_deps.py` stays as the early warning for the next dependency release.
- Installed CLI verified by running `parallax guide` and a storyboard flow from the installed tool.

## Out of Scope

- Dependency upgrades.

## References

- PR #193.
