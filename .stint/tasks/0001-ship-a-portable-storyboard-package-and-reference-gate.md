---
id: "0001"
title: "Ship a portable storyboard package and reference gate"
status: done
priority: p1
size: l
created_at: "2026-10-16T00:15:54Z"
completed_at: "2026-10-16T02:42:51Z"
blocked_by: []
gh_issue: []
area:
  - "cli"
  - "storyboard"
tags:
  - "storyboard"
  - "project-contract"
  - "reference-gates"
---


## Why

Parallax can render a useful offline storyboard, but a fresh project still exposes implementation-shaped paths and relies on the skill to notice missing visual references. The first shippable collaboration surface should be a portable storyboard package that an editor can open without Parallax, while the CLI deterministically records the unresolved creative decision instead of silently generating a style set. This advances Parallax's stated vision of editable YAML artifacts and a deterministic CLI behind each production step.

## Done When

- A fresh storyboard-only project has a documented, visible package layout containing the authored brief, source script/narration and headline breakdown, reference inventory, scene contract, HTML storyboard, thumbnails/contact sheet where available, QA notes, and an editor-facing handoff manifest.
- The handoff package does not require a hidden `.parallax` directory, job stream, approval store, or installed Parallax to be useful to an editor.
- `parallax storyboard` or its project setup companion writes structured, machine-readable reference gates for unanchored editorial scenes and product/UI scenes missing official references; no provider call is made while a required gate is unresolved.
- The operator skill reads those gates and asks the human to choose among supplied references, official-reference discovery, an authorized generated style anchor, or an intentional no-reference path. It never silently chooses paid reference generation.
- Storyboard review flags compound generated-image directions and unsafe vertical text before generation. A generated still is one composition unless an explicit local composite treatment is selected.
- Add a lightweight `drive-parallax` simulated-operator fixture that starts from a fresh script against the local checkout, reaches the HTML storyboard checkpoint, exercises the missing-reference question, and proves no paid generation occurs.
- The fixture uses the current local source (`uv run parallax` or an equivalent explicit checkout command), not an older globally installed CLI.
- Tests cover package creation, the structured reference gate, compound-scene rejection/advisory, and the simulated no-spend storyboard flow.
- Update CLI documentation and the Parallax skill with the supported package contract and review boundary.

## Out of Scope

- General video production, narration, captions, final assembly, or paid still generation.
- Persistent job controller, approval binding, cache/state hierarchy, Pi package UI, or a `.parallax` state directory.
- A generic motion-graphics runtime. This stint may name a future motion-module field, but does not implement SVG/Remotion templates.

## References

- `README.md` Vision: deterministic CLI and editable YAML production artifacts.
- `TRUTH.md` version roadmap and the 2026-10-15 Rise storyboard review.
- PR #190: missing reference, visual variety, compound-frame, and safe-copy advisories.
