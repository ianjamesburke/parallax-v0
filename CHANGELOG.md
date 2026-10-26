# Changelog

Newest releases appear first.
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
