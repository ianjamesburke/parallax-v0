---
id: "0002"
title: "Gate paid generation behind recorded human approval and a budget"
status: todo
priority: p1
size: m
created_at: "2026-10-16T02:09:33Z"
blocked_by: []
gh_issue: []
area:
  - "cli"
tags:
  - "spend-guard"
---

## Why

An agent running `parallax produce` or `parallax image generate` non-interactively can spend real money with no human approval. `prompt_proceed` (`src/parallax/preflight.py:305-319`) proceeds automatically when `--yes` is passed or stdout is not a TTY. The only other guard is a $0.50 minimum balance (`src/parallax/produce.py:226-247`). `image generate` has no cost gate, and `produce --scene` appears even less guarded. This caused a real accidental ~$0.0175 spend during stint 0001. The reference gates stop unanchored production but not unapproved spend.

## Done When

- Spend above an explicit, required configured budget is refused unless a human-recorded approval for that run exists; `--yes` alone does not grant it.
- `produce` (all paths including `--plan` and `--scene`) and `image generate` share one cost gate with an estimate and the same approval record.
- Missing budget config throws with what/where/fix; no silent fallback.
- The approval requirement is exposed in `parallax guide` and gate-style JSON so a skill-free agent can ask the human.
- Tests assert zero provider calls without approval.

## Out of Scope

- Persistent job controller and Pi UI.

## References

- PR #191 (stint 0001), TRUTH.md "Stint 0001 status".
