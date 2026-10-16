---
id: "0006"
title: "Clean storyboard HTML layout and remove opening shots section"
status: done
priority: p1
size: s
created_at: "2026-10-16T15:14:00Z"
completed_at: "2026-10-16T15:19:18Z"
blocked_by: []
gh_issue: []
area:
  - "storyboard"
tags:
  - "storyboard"
---

# Clean storyboard HTML layout and remove opening shots section

## Goal

Redesign `storyboard.html` (`src/parallax/storyboard.py`) so it is clean, airy, and easy to scan:
1. Remove the redundant "Opening shots — first 12 seconds" section (`opening-grid` / `opening-shot`).
2. Remove verbose instructional boilerplate at the top of the page (Checkpoint 1 prose block, expanded pacing checklists when there are no timing warnings) and collapse non-blocking pacing notes into a compact `<details>` disclosure.
3. Make reference images compact (`~88px` in the top reference bar and `44px` mini-thumbnails in per-scene reference strips) instead of full-sized 280px–330px cards.
4. Redesign `.scene` cards into a clean 2-column layout (`220px` media column on the left + scannable script/direction column on the right) without repetitive `<h3>` headings or boilerplate disclaimers on every card.

## Done When

1. `src/parallax/storyboard.py` renders the redesigned clean HTML layout with smaller reference thumbnails, no "Opening shots — first 12 seconds" section, and compact scene cards.
2. `tests/test_opening_pacing.py` and `tests/test_storyboard.py` pass with `OPENROUTER_API_KEY` unset.
3. All 6 E2E storyboard packages under `runs/2026-10-16-storyboard-e2e/` are regenerated with the new HTML template.

## Out of Scope

- Changing `handoff.json`, `scenes.yaml`, `references.yaml`, or `contact-sheet.jpg` schemas.
