---
id: "0007"
title: "Storyboard PDF export and Rise infographic storyboard"
status: done
priority: p1
size: m
created_at: "2026-10-16T16:33:00Z"
completed_at: "2026-10-16T16:44:00Z"
blocked_by: []
gh_issue: []
area:
  - "storyboard"
tags:
  - "storyboard"
  - "pdf"
  - "infographic"
---

# Storyboard PDF export and Rise infographic storyboard

## Goal

1. Support automatic PDF export in `parallax storyboard` (`storyboard.pdf`) using headless Chrome/Chromium when available, with clean `@media print` CSS pagination (cards never cut in half, letter size margins).
2. Expose `--pdf / --no-pdf` flag in the CLI and record `storyboard.pdf` in `handoff.json` `files`.
3. Provide high-quality 2D Infographic Show style stills and graphics for Rise Cortisol, matching `references/infographic-style.png` (2D flat vector aesthetic, pastel palette, clean characters, infographic charts).
4. Run a simulated E2E pass for Rise to generate the complete storyboard package with both `storyboard.html` and `storyboard.pdf`.

## Done When

1. `storyboard_package.py` and `cli/_produce.py` generate `storyboard.pdf` when a PDF engine is available and include it in `handoff.json`.
2. All unit tests pass with `OPENROUTER_API_KEY` unset.
3. The Rise infographic storyboard package contains all 26 scenes in the infographic vector style with valid `storyboard.html` and `storyboard.pdf`.
