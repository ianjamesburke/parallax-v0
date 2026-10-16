---
id: "0003"
title: "Retire the operator skill by moving its knowledge into the CLI"
status: todo
priority: p2
size: l
created_at: "2026-10-16T03:01:58Z"
blocked_by: []
gh_issue: []
area:
  - "cli"
tags:
  - "skill-free"
---

## Why

The storyboard stage is proven skill-free (four operator rounds, plus a run on the installed v0.5.45: `--help` and `parallax guide` only, no crash, ready_for_review). The operator skill (`skills/parallax-v0/SKILL.md`, 358 lines, plus `references/{commands,motion-graphics,voiceover-broll}.md`) still holds knowledge the CLI does not expose: the plan/produce loop details, asset locking, model selection, cost management, motion-graphic clip convention, voiceover and b-roll rules. Deleting it now would regress every stage after storyboard review. install.sh downloads it from main (install.sh:94) and scripts/check-skill-version.sh depends on it.

## Done When

- Every instruction in the skill and its references is either exposed as parsable fields in `parallax guide` / `parallax schema` / command `--help` / advisories, or deliberately dropped with a recorded reason.
- A skill-free operator completes a run past the storyboard (plan, human spend approval handoff, test-mode produce, locking, a motion-graphic scene, voiceover) using only the installed CLI.
- Then: delete the skill and references, drop the install.sh skill download and check-skill-version.sh, update src/parallax/storyboard.py:170 (cites motion-graphics.md), README, and the narrative-content skills that reference parallax.
- No stub or compat shim left behind.

## Out of Scope

- New production features.

## References

- PRs #191, #192, #193; TRUTH.md "Goal and limiting factor".
