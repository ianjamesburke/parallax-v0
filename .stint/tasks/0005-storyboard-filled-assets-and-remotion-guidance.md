---
id: "0005"
title: "Sync generated stills/clips into brief.yaml and storyboard-package; add Remotion skill guidance"
status: done
priority: p1
size: m
created_at: "2026-10-16T07:41:00Z"
completed_at: "2026-10-16T08:02:43Z"
blocked_by: []
gh_issue: []
area:
  - "cli"
  - "storyboard"
tags:
  - "storyboard"
  - "remotion"
---

## Why

1. `parallax storyboard` renders `storyboard-package/storyboard.html` and copies referenced images/videos into `storyboard-package/assets/`, but `parallax produce` only locks generated `still_path` and `clip_path` into `plan.yaml` (`parallax/scratch/plan.yaml`), never back into `brief.yaml` or `storyboard-package/`. As a result, generating stills leaves `storyboard.html` empty unless the operator manually edits `brief.yaml`.
2. `brief.to_plan_skeleton()` and `Plan` reject `scene_type: motion_graphic` without `clip_path` even during a `stills_only` pass, blocking AI still generation for the non-graphic scenes before motion-graphic MP4s are rendered.
3. `undeclared_image_refs` rejects a scene `image_refs` entry that references a prior scene's `still_path` for continuity.
4. `parallax guide motion_graphic` and the missing-clip advisory dropped the instruction to use the Remotion skill (`remotion-best-practices` / `create-remotion-geist`) when available in the agent environment.
5. `just pr-install` in `justfile` does not pass `uv.lock` `--constraints`.

## Done When

- `parallax guide motion_graphic` (`renderer` and `workflow`), `_missing_clip_advisory` in `src/parallax/storyboard.py`, and `docs/skill-retirement.md` explicitly instruct the agent to use the Remotion skill if available when generating/authoring `scene_type: motion_graphic` posters and clips.
- `parallax plan` and `parallax produce` accept `--stills-only` (`stills_only: true`).
- `brief.to_plan_skeleton()` and `Plan` allow `scene_type: motion_graphic` without `clip_path` during planning and `stills_only` runs; full `produce` (`not stills_only`) still fails fast in preflight and `stage_stills` if a `motion_graphic` scene has no `clip_path`.
- When `produce` locks a `still_path` or `clip_path` into a plan that has `source_brief` (in real mode), it also writes that `still_path` or `clip_path` into the matching scene of `source_brief` (`brief.yaml`, preserving comments) and refreshes `<folder>/storyboard-package/` at the end of the run so `storyboard.html`, `contact-sheet.jpg`, and `assets/` contain the generated images and videos.
- `undeclared_image_refs` accepts another scene's `still_path` as a valid continuity reference in `image_refs`.
- `just pr-install` installs with `--constraints` exported from `uv.lock`.
- Full pytest suite passes.

## Out of Scope

- Bundling Remotion as a Python/Node dependency of Parallax.
