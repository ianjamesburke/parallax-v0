# Changelog

Newest releases appear first.

## [0.7.7] — 2026-11-04

### Changes
- docs: default to storyboard PDF and stills review in chat, no upload unless asked, and still generation before the first review; approval gates animated scene/video generation and other paid video spend. After approval, use the requester’s animation tool or configured Parallax, import scenes, and follow the footage workflow.
- docs: clarify paid still generation, the no-key agent-tool fallback, and image import plus explicit brief still_path fields. Final delivery mirrors the whole project folder to Google Drive as-is using the agent’s connection and returns folder and MP4 links.
- tests: pin these defaults in the guide start map, front_half, footage_job, and README.

## [0.7.6] — 2026-11-04

### Changes
- fix: unused imported clips are warnings, so media checks pass when unused clips are the only issue. Francisco asks Muse for about 30 scenes to provide options; the brief picks clips that fit the narration in Scene_NN order, and extra takes and options stay in the folder unused.
- docs: remove the requirement to use every clip while preserving preferred-take selection for scenes the brief uses.

## [0.7.5] — 2026-11-04

### Changes
- fix: produce supplied footage and voiceover through the brief workflow at zero provider cost, with imported take selection, coverage checks and cached audio processing.
- docs: reverse 7ee1e46 (PR #212, 2026-10-29): route supplied footage through brief/plan so each scene keeps its narration; hand-written cut maps led Muse to map sentence N to Scene_NN and drop PA21 Scenes 13–25. `cut` remains a loose-clips tool and supports the editing@ agent.

## [0.7.4] — 2026-11-04

### Changes
- fix: WhisperX alignment no longer requires or downloads NLTK punkt_tab data.
## [0.7.3] — 2026-11-03

### Changes
- feat: project-level `references:` list (id, kind, description, status, paths) cited by scenes with `refs: [id]`; old per-scene `brand_ref` still loads and maps over. Guide rule: only the product's own logo, packaging, app UI or product photo counts, the character's own phone, inbox and apps do not. `check` prints one "References needed" warning; unknown ids and nonexistent supplied paths are errors. Text-only storyboard PDFs render with a bundled pure-Python renderer (fpdf2 + DejaVu Sans), so a clean `uv tool install` needs no browser or system libraries (#222)
## [0.7.2] — 2026-11-03

### Changes
- fix: text-only storyboard keeps the normal scene layout with a placeholder image frame, "Scene 01" headings, narration as the next-largest line and normal text size. `brand_ref: missing` is a warning, not a block; the keyword "looks branded" heuristic is gone; nonexistent supplied paths stay an error; stray "· None" removed. Light install: whisperx moved to the optional `align` extra and lazy-imported, so guide, schema, check, validate, storyboard and assets import install in seconds without torch; the repo checkout keeps whisperx through a default dependency group (#221)
## [0.7.1] — 2026-11-03

### Changes
- fix: text-only storyboard reads on a phone (heading "Scene 01", narration as the next-largest line, larger body text, portrait pages, no per-scene titles); shared style shows once on the cover. Brand gate driven by per-scene `brand_ref`: `missing` warns and `supplied` with absent files errors in `parallax check`; the storyboard notes missing references on the cover. Guide tells agents to set `brand_ref` on every scene and collect official assets before media generation (#220)
## [0.7.0] — 2026-11-03

### Changes
- feat: front half for outside agents. `parallax storyboard --text-only --pdf` builds a review PDF with no media or provider calls (one page per scene: Scene_NN, VO, estimated interval, min usable footage, generation prompt, brand-ref status). New read-only `parallax check` lists what does not "compile" in a project with a fix for each (`--json`, `--stage draft|review|media`). New `parallax assets import` renames incoming clips to Scene_NN.mp4 / Scene_NN_takeNN.mp4 and keeps `source_filename`. Brief gains scene_id, purpose, generation_method, brand_ref, takes, timing {estimate_wps 3.0, speed 1.0, handle_s 1.0}; estimates move from 150 wpm to 3.0 words/s x speed; min usable = duration + 1s. `parallax guide` leads with the front-half workflow and tells agents to run `parallax check` before handoff (#219)
## [0.6.3] — 2026-11-02

### Changes
- fix(vo-verify): with `--audio` + `--words`, verification trusts the supplied words.json (script diff + timing/coverage sanity checks) instead of transcribing with whisper, ending false `vo_verify_failed` from partial whisper transcripts; whisper transcripts that leave audio uncovered are retried (whole file, then 25 s windows) and reported as `vo_transcription_incomplete` without paid TTS regeneration; reports carry `source` (`words_json` | `whisper`) and `reason`
- feat(captions): default chunking is 1-2 words per caption; a 3rd word joins only when at most one of the three is a content word (small function words like a/is/of/what/here's ride along: "Here's what a", "Filler is a"; "gel injected into" splits). Applies to `smart`/default and integer 3 everywhere (burn, `cut` now defaults to `smart`, plan, local video, SRT); integer 1/2 cap lower. New style key `three_word_rule` (`function_words` default | `letters` | `any`), `three_word_max_letters` default 14; no lone dangling function words
- feat(captions): captions are always a single line of 1-2 words (3 max) for every preset — new style keys `max_lines` (default 1), `max_words` (default 3; larger `words_per_chunk` clamps to it) and `three_word_max_letters` (default 12); chunks too wide for one line split into more chunks timed from their own words (karaoke highlight still tracks the spoken word); `soft` widens to `max_line_width` 0.72; SRT export follows the same word limits
- feat(captions): `karaoke` preset (per-word drawtext, filled highlight box on the active word; `highlight_color`/`highlight_padding`) and `soft` preset (lowercase, two narrow lines, blurred shadow); new `lowercase` and `shadow_blur`/`shadow_color`/`shadow_opacity` style options
- fix(cut): `--caption-style` no longer str()-coerces dict styles, and unknown preset names (CLI, manifest, resolver) raise an error listing valid presets instead of silently using bangers
- fix(captions): missing style fonts (e.g. Mac-only Impact path) fall back to a bundled bold font with a warning instead of ffmpeg's default font
- fix(produce): default plan narration to Gemini Charon while retaining `tts-mini` / `nova` for explicit or legacy named mini voices
- docs(cut): document `--vo-retries 0` as the fail-closed TTS-spend cap
- fix: send Veo Lite's `resolution: 720p` by default with audio disabled ($0.03/s); silent 1080p is about $0.05/s
- fix(audio): default standalone `audio voiceover` narration to Gemini Charon, while preserving `nova` for an explicit `tts-mini` selection
- fix(cut): retry a failed per-chunk ASR verification once before evicting cached audio or regenerating TTS
- docs(guide): route script-plus-footage workflows directly to `parallax cut`

## [0.6.2] — 2026-10-26

### Changes
- fix: parallax update handles editable installs
## [0.6.1] — 2026-10-26

### Changes
- fix: accept --output as alias for --out (#184)
## [0.6.0] — 2026-10-26

### Changes
- docs(readme): agent start block, OPENROUTER_API_KEY optional
- feat(export): editor handoff package (xmeml v4 + FCPXML 1.10 timeline, assets, SRT, README) (#204)
- feat: HTML/SVG + GSAP motion-graphic styles (5 presets, VO-timed, layout guard) (#203)
- feat: show opening headlines from frame zero (#202)
- feat: editorial storyboard standard + PDF export + --from-video (#201)
- fix: on-screen text never cropped (captions newline, zoom rounding) (#200)
- fix: agent single-shot path + branded motion graphics, full-bleed app screens, caption sizing (#199)
- chore: untrack .stint and add to .gitignore
- feat(stages): archive regenerated scene assets into parallax/assets/versions/ and prune intermediate video/tts build files
- feat(cli): write root storyboard.pdf and exports/ deliverable, hardlink storyboard package assets, document broll_reuse
- fix(cli): ignore mock stubs in preflight overwrite check, support multi-kind ref layering, and add ready_for_plan
- fix(cli): run voiceover+align before animate, protect real assets in test mode, improve CLI UX and brand gates
- docs: add stint 0008 for Rise illustrated stills and minimal graphics
- docs: mark stint 0007 done
## [0.5.50] — 2026-10-16

### Changes
- feat: make headlines and opening titles fully visible on frame 0 by default; retain explicit fade-in overrides
- fix: keep caption, headline, and motion-graphic text inside safe bounds during wrapping and animated scaling
- feat: add printable PDF export to storyboard package (#198)
- chore(stint): mark 0006 done
- feat(storyboard): redesign HTML layout with compact references and no opening-shots grid (#197)
- chore: mark stint 0005 done
- feat(storyboard): sync generated assets to brief/package, add --stills-only and Remotion guidance (#196)
- chore: add stint 0005 (storyboard asset sync and Remotion skill guidance)
- docs: move ship-parallax and create-skill to global ~/.agents/skills
- docs: move ship-parallax to .agents/skills; add create-skill; track .agents/skills
- docs: ship-parallax skill covers stint, operator e2e, release
- Remove the spend approval gate; paid commands print the estimate (#195)
- chore: mark stints 0003 and 0004 done
- Retire the operator skill into the CLI; install locked deps (stints 0003, 0004) (#194)
- chore: add stints 0003 (retire operator skill) and 0004 (locked installs)
- fix: CLI works on typer 0.27 (vendored click): guide, schema cli, exit codes (#193)
- chore: mark stints 0001 and 0002 done
- chore: add stint 0002 (gate paid generation behind recorded approval)
- chore: add limiting-factor rule to CLAUDE.md
- feat: gate paid generation behind a recorded human spend budget (stint 0002) (#192)
- feat: portable storyboard package, reference gates, self-describing CLI (stint 0001) (#191)
- chore: ignore local agent and browser tool state
- chore: add stint workspace with task 0001 (portable storyboard package and reference gate)
- docs: stage storyboard and narrated draft approval before animation
- fix(storyboard): catch compound frames and unsafe copy
- fix(storyboard): flag missing references and graphic repetition
- Tighten acoustic opening and audit opening scene pacing
- Cap acoustic silence at sentence boundaries without clipping speech
- Align scene cuts with incoming words
- Preserve narration onsets and respect caption sentence boundaries
- Clarify voiceover B-roll action and graphic layout review
- feat: separate headlines and develop named storyboard references
- feat: add typed HTML storyboards and local motion graphic scenes
- fix: scope Parallax inputs to project folder
- fix: simplify E2E agent prompt
- feat: accept E2E fixture inputs
- feat: add hermetic agent intake launcher
- fix(skill): sync operator guidance with current CLI
- merge: sync alpha with latest remote changes
- Cheaper, accurate costs: live-verified catalog, billed usage, compact models list (#186)
- feat(skill): always surface PR URL with testing instructions
- refactor(skill): drop feature/ prefix from branch naming
- refactor: split bump-and-install into separate commands; add pr-clean-merged cleanup script
- docs: update ship-parallax skill docs to split bump-and-install into separate commands
- fix: use osascript password dialog on macOS for API key entry
- feat: symlink parallax skill into pi, openclaw, opencode, and codex agent dirs
## [0.5.49] — 2026-10-16

### Changes
- chore(stint): mark 0006 done
- feat(storyboard): redesign HTML layout with compact references and no opening-shots grid (#197)
- chore: mark stint 0005 done
- feat(storyboard): sync generated assets to brief/package, add --stills-only and Remotion guidance (#196)
- chore: add stint 0005 (storyboard asset sync and Remotion skill guidance)
- docs: move ship-parallax and create-skill to global ~/.agents/skills
- docs: move ship-parallax to .agents/skills; add create-skill; track .agents/skills
- docs: ship-parallax skill covers stint, operator e2e, release
- Remove the spend approval gate; paid commands print the estimate (#195)
- chore: mark stints 0003 and 0004 done
- Retire the operator skill into the CLI; install locked deps (stints 0003, 0004) (#194)
- chore: add stints 0003 (retire operator skill) and 0004 (locked installs)
- fix: CLI works on typer 0.27 (vendored click): guide, schema cli, exit codes (#193)
- chore: mark stints 0001 and 0002 done
- chore: add stint 0002 (gate paid generation behind recorded approval)
- chore: add limiting-factor rule to CLAUDE.md
- feat: gate paid generation behind a recorded human spend budget (stint 0002) (#192)
- feat: portable storyboard package, reference gates, self-describing CLI (stint 0001) (#191)
- chore: ignore local agent and browser tool state
- chore: add stint workspace with task 0001 (portable storyboard package and reference gate)
- docs: stage storyboard and narrated draft approval before animation
- fix(storyboard): catch compound frames and unsafe copy
- fix(storyboard): flag missing references and graphic repetition
- Tighten acoustic opening and audit opening scene pacing
- Cap acoustic silence at sentence boundaries without clipping speech
- Align scene cuts with incoming words
- Preserve narration onsets and respect caption sentence boundaries
- Clarify voiceover B-roll action and graphic layout review
- feat: separate headlines and develop named storyboard references
- feat: add typed HTML storyboards and local motion graphic scenes
- fix: scope Parallax inputs to project folder
- fix: simplify E2E agent prompt
- feat: accept E2E fixture inputs
- feat: add hermetic agent intake launcher
- fix(skill): sync operator guidance with current CLI
- merge: sync alpha with latest remote changes
- Cheaper, accurate costs: live-verified catalog, billed usage, compact models list (#186)
- feat(skill): always surface PR URL with testing instructions
- refactor(skill): drop feature/ prefix from branch naming
- refactor: split bump-and-install into separate commands; add pr-clean-merged cleanup script
- docs: update ship-parallax skill docs to split bump-and-install into separate commands
- fix: use osascript password dialog on macOS for API key entry
- feat: symlink parallax skill into pi, openclaw, opencode, and codex agent dirs
## [0.5.48] — 2026-10-16

### Changes
- chore: mark stint 0005 done
- feat(storyboard): sync generated assets to brief/package, add --stills-only and Remotion guidance (#196)
- chore: add stint 0005 (storyboard asset sync and Remotion skill guidance)
- docs: move ship-parallax and create-skill to global ~/.agents/skills
- docs: move ship-parallax to .agents/skills; add create-skill; track .agents/skills
- docs: ship-parallax skill covers stint, operator e2e, release
- Remove the spend approval gate; paid commands print the estimate (#195)
- chore: mark stints 0003 and 0004 done
- Retire the operator skill into the CLI; install locked deps (stints 0003, 0004) (#194)
- chore: add stints 0003 (retire operator skill) and 0004 (locked installs)
- fix: CLI works on typer 0.27 (vendored click): guide, schema cli, exit codes (#193)
- chore: mark stints 0001 and 0002 done
- chore: add stint 0002 (gate paid generation behind recorded approval)
- chore: add limiting-factor rule to CLAUDE.md
- feat: gate paid generation behind a recorded human spend budget (stint 0002) (#192)
- feat: portable storyboard package, reference gates, self-describing CLI (stint 0001) (#191)
- chore: ignore local agent and browser tool state
- chore: add stint workspace with task 0001 (portable storyboard package and reference gate)
- docs: stage storyboard and narrated draft approval before animation
- fix(storyboard): catch compound frames and unsafe copy
- fix(storyboard): flag missing references and graphic repetition
- Tighten acoustic opening and audit opening scene pacing
- Cap acoustic silence at sentence boundaries without clipping speech
- Align scene cuts with incoming words
- Preserve narration onsets and respect caption sentence boundaries
- Clarify voiceover B-roll action and graphic layout review
- feat: separate headlines and develop named storyboard references
- feat: add typed HTML storyboards and local motion graphic scenes
- fix: scope Parallax inputs to project folder
- fix: simplify E2E agent prompt
- feat: accept E2E fixture inputs
- feat: add hermetic agent intake launcher
- fix(skill): sync operator guidance with current CLI
- merge: sync alpha with latest remote changes
- Cheaper, accurate costs: live-verified catalog, billed usage, compact models list (#186)
- feat(skill): always surface PR URL with testing instructions
- refactor(skill): drop feature/ prefix from branch naming
- refactor: split bump-and-install into separate commands; add pr-clean-merged cleanup script
- docs: update ship-parallax skill docs to split bump-and-install into separate commands
- fix: use osascript password dialog on macOS for API key entry
- feat: symlink parallax skill into pi, openclaw, opencode, and codex agent dirs
## [0.5.47] — 2026-10-16

### Changes
- Remove the spend approval gate; paid commands print the estimate (#195)
- chore: mark stints 0003 and 0004 done
- Retire the operator skill into the CLI; install locked deps (stints 0003, 0004) (#194)
- chore: add stints 0003 (retire operator skill) and 0004 (locked installs)
- fix: CLI works on typer 0.27 (vendored click): guide, schema cli, exit codes (#193)
- chore: mark stints 0001 and 0002 done
- chore: add stint 0002 (gate paid generation behind recorded approval)
- chore: add limiting-factor rule to CLAUDE.md
- feat: gate paid generation behind a recorded human spend budget (stint 0002) (#192)
- feat: portable storyboard package, reference gates, self-describing CLI (stint 0001) (#191)
- chore: ignore local agent and browser tool state
- chore: add stint workspace with task 0001 (portable storyboard package and reference gate)
- docs: stage storyboard and narrated draft approval before animation
- fix(storyboard): catch compound frames and unsafe copy
- fix(storyboard): flag missing references and graphic repetition
- Tighten acoustic opening and audit opening scene pacing
- Cap acoustic silence at sentence boundaries without clipping speech
- Align scene cuts with incoming words
- Preserve narration onsets and respect caption sentence boundaries
- Clarify voiceover B-roll action and graphic layout review
- feat: separate headlines and develop named storyboard references
- feat: add typed HTML storyboards and local motion graphic scenes
- fix: scope Parallax inputs to project folder
- fix: simplify E2E agent prompt
- feat: accept E2E fixture inputs
- feat: add hermetic agent intake launcher
- fix(skill): sync operator guidance with current CLI
- merge: sync alpha with latest remote changes
- Cheaper, accurate costs: live-verified catalog, billed usage, compact models list (#186)
- feat(skill): always surface PR URL with testing instructions
- refactor(skill): drop feature/ prefix from branch naming
- refactor: split bump-and-install into separate commands; add pr-clean-merged cleanup script
- docs: update ship-parallax skill docs to split bump-and-install into separate commands
- fix: use osascript password dialog on macOS for API key entry
- feat: symlink parallax skill into pi, openclaw, opencode, and codex agent dirs
## [0.5.46] — 2026-10-16

### Changes
- chore: mark stints 0003 and 0004 done
- Retire the operator skill into the CLI; install locked deps (stints 0003, 0004) (#194)
- chore: add stints 0003 (retire operator skill) and 0004 (locked installs)
- fix: CLI works on typer 0.27 (vendored click): guide, schema cli, exit codes (#193)
- chore: mark stints 0001 and 0002 done
- chore: add stint 0002 (gate paid generation behind recorded approval)
- chore: add limiting-factor rule to CLAUDE.md
- feat: gate paid generation behind a recorded human spend budget (stint 0002) (#192)
- feat: portable storyboard package, reference gates, self-describing CLI (stint 0001) (#191)
- chore: ignore local agent and browser tool state
- chore: add stint workspace with task 0001 (portable storyboard package and reference gate)
- docs: stage storyboard and narrated draft approval before animation
- fix(storyboard): catch compound frames and unsafe copy
- fix(storyboard): flag missing references and graphic repetition
- Tighten acoustic opening and audit opening scene pacing
- Cap acoustic silence at sentence boundaries without clipping speech
- Align scene cuts with incoming words
- Preserve narration onsets and respect caption sentence boundaries
- Clarify voiceover B-roll action and graphic layout review
- feat: separate headlines and develop named storyboard references
- feat: add typed HTML storyboards and local motion graphic scenes
- fix: scope Parallax inputs to project folder
- fix: simplify E2E agent prompt
- feat: accept E2E fixture inputs
- feat: add hermetic agent intake launcher
- fix(skill): sync operator guidance with current CLI
- merge: sync alpha with latest remote changes
- Cheaper, accurate costs: live-verified catalog, billed usage, compact models list (#186)
- feat(skill): always surface PR URL with testing instructions
- refactor(skill): drop feature/ prefix from branch naming
- refactor: split bump-and-install into separate commands; add pr-clean-merged cleanup script
- docs: update ship-parallax skill docs to split bump-and-install into separate commands
- fix: use osascript password dialog on macOS for API key entry
- feat: symlink parallax skill into pi, openclaw, opencode, and codex agent dirs
## [0.5.45] — 2026-10-16

### Changes
- fix: CLI works on typer 0.27 (vendored click): guide, schema cli, exit codes (#193)
- chore: mark stints 0001 and 0002 done
- chore: add stint 0002 (gate paid generation behind recorded approval)
- chore: add limiting-factor rule to CLAUDE.md
- feat: gate paid generation behind a recorded human spend budget (stint 0002) (#192)
- feat: portable storyboard package, reference gates, self-describing CLI (stint 0001) (#191)
- chore: ignore local agent and browser tool state
- chore: add stint workspace with task 0001 (portable storyboard package and reference gate)
- docs: stage storyboard and narrated draft approval before animation
- fix(storyboard): catch compound frames and unsafe copy
- fix(storyboard): flag missing references and graphic repetition
- Tighten acoustic opening and audit opening scene pacing
- Cap acoustic silence at sentence boundaries without clipping speech
- Align scene cuts with incoming words
- Preserve narration onsets and respect caption sentence boundaries
- Clarify voiceover B-roll action and graphic layout review
- feat: separate headlines and develop named storyboard references
- feat: add typed HTML storyboards and local motion graphic scenes
- fix: scope Parallax inputs to project folder
- fix: simplify E2E agent prompt
- feat: accept E2E fixture inputs
- feat: add hermetic agent intake launcher
- fix(skill): sync operator guidance with current CLI
- merge: sync alpha with latest remote changes
- Cheaper, accurate costs: live-verified catalog, billed usage, compact models list (#186)
- feat(skill): always surface PR URL with testing instructions
- refactor(skill): drop feature/ prefix from branch naming
- refactor: split bump-and-install into separate commands; add pr-clean-merged cleanup script
- docs: update ship-parallax skill docs to split bump-and-install into separate commands
- fix: use osascript password dialog on macOS for API key entry
- feat: symlink parallax skill into pi, openclaw, opencode, and codex agent dirs
## [0.5.44] — 2026-10-16

### Changes
- chore: add stint 0002 (gate paid generation behind recorded approval)
- chore: add limiting-factor rule to CLAUDE.md
- feat: gate paid generation behind a recorded human spend budget (stint 0002) (#192)
- feat: portable storyboard package, reference gates, self-describing CLI (stint 0001) (#191)
- chore: ignore local agent and browser tool state
- chore: add stint workspace with task 0001 (portable storyboard package and reference gate)
- docs: stage storyboard and narrated draft approval before animation
- fix(storyboard): catch compound frames and unsafe copy
- fix(storyboard): flag missing references and graphic repetition
- Tighten acoustic opening and audit opening scene pacing
- Cap acoustic silence at sentence boundaries without clipping speech
- Align scene cuts with incoming words
- Preserve narration onsets and respect caption sentence boundaries
- Clarify voiceover B-roll action and graphic layout review
- feat: separate headlines and develop named storyboard references
- feat: add typed HTML storyboards and local motion graphic scenes
- fix: scope Parallax inputs to project folder
- fix: simplify E2E agent prompt
- feat: accept E2E fixture inputs
- feat: add hermetic agent intake launcher
- fix(skill): sync operator guidance with current CLI
- merge: sync alpha with latest remote changes
- Cheaper, accurate costs: live-verified catalog, billed usage, compact models list (#186)
- feat(skill): always surface PR URL with testing instructions
- refactor(skill): drop feature/ prefix from branch naming
- refactor: split bump-and-install into separate commands; add pr-clean-merged cleanup script
- docs: update ship-parallax skill docs to split bump-and-install into separate commands
- fix: use osascript password dialog on macOS for API key entry
- feat: symlink parallax skill into pi, openclaw, opencode, and codex agent dirs
## [0.5.43] — 2026-05-19

### Changes
- fix: use osascript password dialog on macOS for API key entry
- feat: symlink parallax skill into pi, openclaw, opencode, and codex agent dirs
