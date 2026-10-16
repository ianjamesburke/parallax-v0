# Operator skill retirement (stint 0003)

The operator skill (`skills/parallax-v0/`, 358-line SKILL.md plus three references) is deleted. Every instruction in it is either exposed by the CLI or dropped below. Crawl: `parallax guide` (map and topic index), `parallax guide <topic>`, `parallax schema brief|plan|cli`, `parallax <cmd> --help`, storyboard/validate advisories, a refused paid command's JSON.

Dispositions: **G** = `parallax guide <topic>`, **A** = advisory or error already emitted by a command, **H** = `--help` or `schema`, **D** = dropped.

## SKILL.md

| Skill content | Disp | Where / reason |
|---|---|---|
| Version check, `parallax update`, "warn if skill is behind" | D | The skill is gone; `parallax --version` and `parallax update` are in `--help`. |
| Workflow map is `parallax guide` | G | Already the source of truth. |
| `OPENROUTER_API_KEY` required | H | Paid commands name it when refused; `parallax credits --help`. |
| Intake: ask only unresolved questions; folder is the whole input boundary | G | `guide intake` (`human_decision`, `boundary`). |
| Performance mode (voiceover vs presenter; first person is not an avatar) | G | `guide intake`, `guide voiceover_broll` (`mode`). |
| Brand references from official site, provenance, real logos not generated | G | `guide brief` (`scene_craft.reference_provenance`); gate choice `official` already in `storyboard --json`. |
| Reference sets (character sheet, env frame), pending until approved | G | `guide brief` (`scene_craft.reference_sets`); `review_status` rule in `reference_gates`. |
| Per-scene refs beat global refs for diagrams | G | `guide brief` (`scene_craft.global_vs_scene_refs`). |
| Headline vs body split, headline lasts through first scene | G | `script_mapping` (existing), `scene_craft.headline` (verified `stages.py` headline `end_time_s`). |
| concept_name proposal, never invent concept_id | G | Stage `brief` `review_boundary` (existing), `scene_craft.identity`. |
| One beat per scene, split beyond ~5s, 1-4s social shots | G | `scene_craft.one_beat`; opening pacing is advisory kinds `opening_*`. |
| Compound direction goes to separate beats | A | `compound_direction` advisory; `scene_craft.compound_direction`. |
| On-screen copy is layout, safe margins for 9:16 | A | `unsafe_vertical_text` advisory; `scene_craft.on_screen_text_layout`. |
| Script from scratch: hook in 1-2s, 15-45s | G | `scene_craft.script_from_scratch`. |
| Show storyboard.html before generation; no second scene table | G | Stage `storyboard_review`. |
| Style note must be in each image prompt; plan `style` is voice-only | G | Verified: `style` is read only by voiceover (`stages.py` stage_voiceover). `scene_craft.style`, `guide models`. |
| Reference gates are CLI output | A | `storyboard --json` `gates`, `reference resolve --help`. |
| Visual variety: motion graphics under ~40%, no runs of 3 | A | `motion_graphic_share`, `motion_graphic_run` advisories. |
| Review checkpoints 1-4 (storyboard, stills, narrated draft, final) | G | `guide produce_loop` (`review_checkpoints`). |
| Scene_type vs shot_type meaning; animate consistency | G | Existing `scene_fields`; `normalize_scene_type` rejects conflicts. |
| Checkpoints are conversational, simulated replies cannot authorize | G | `produce_loop.checkpoints_note`. |
| Core loop: brief -> plan -> produce; one-shot and two-step | G | `produce_loop` (`flow`, `one_shot`, `paths`). Plan path corrected to `parallax/scratch/plan.yaml` (verified `planner.py`). |
| `produce ... -y` non-interactive | D | Stale: no `-y` or `--yes` exists on any command (verified `produce --help`, test in `test_guide.py`). |
| `--resolution` "sets both output and animate resolution" | G | Corrected: it writes plan `resolution` only (`_inject_resolution`). `produce_loop.produce_flags`. |
| `validate` before `produce` | G | `produce_loop.flow`. |
| Absolute `--folder` paths; quote spaces | G | `produce_loop.paths.folder`. Quoting dropped (shell basics). |
| brief.yaml format and example | H | `schema brief`, `guide brief` example (existing, validated by test). |
| Per-scene `image_refs`, `character_reference`, `assets.provided` footage | G | Existing `brief_fields`, `reference_gates.declaration`. |
| `still_path`/`clip_path` in brief and plan; motion graphic needs clip | G | `asset_locking`, `motion_graphic.contract`. |
| plan.yaml settings listing | H | `schema plan` lists every field and default. Stale: skill said `video_model` default `mid`; the default is `draft`. |
| Image model tiers/prices; `veo-lite`, `wan`, `kling`, `seedream`, `grok-image` | G | Stale list; `guide models` points at `models list/show` (catalog is the source). |
| `tts-gemini` plus `[emotional]` tags for pacing | G | `models.tts` (verified `openrouter/__init__.py`). |
| Caption recipes, `words_per_chunk`, portrait `y_expr` | G | `voiceover_broll.captions` (verified `captions/styles.py`, `burn.py`). |
| Asset locking (still, clip, reference, audio at top level) | G | `asset_locking`. Corrected: `produce` auto-locks stills and clips into the plan; audio locks stay manual unless `voice_postprocess` runs. |
| `output/vN/plan.yaml` is a read-only snapshot; working plan is `parallax/plan.yaml` | G | Snapshot kept (`asset_locking.rules`). Working-plan path was stale: it is `parallax/scratch/plan.yaml` or `--plan`/`--out`. |
| Models and utility commands | G/H | `guide models`, `guide cost`, `models list/show`, each command `--help`. |
| Dry run with `PARALLAX_TEST_MODE=1` | G | `produce_loop.dry_run`, `prerequisites`, `resolution` (new, verified). |
| Open generated media for the human | G | `produce_loop.after_each_artifact`. |
| Inspect visual output against scene/reference map before progressing | G | Same field. |
| Output layout tree | G | `produce_loop.paths.run_output` (corrected to include `assets/`, `scratch/`). |
| Rules: lock before re-run, never edit snapshot | G | `asset_locking.rules`. |
| `--out` not `--output` | D | Stale: `audio cap-sentence-silence` uses `--output`; flags are in each `--help` and `schema cli`. |
| `draft` image model ignores aspect | D | Stale: `models show draft` lists all five aspects as honored; produce checks and normalizes aspect (`stills.py`). |
| Spend, no approval step; estimate printed first | G | `guide cost.approval`. |
| macOS screenshot filename unicode | D | Host-shell tip, not Parallax behavior. |

## references/commands.md

| Content | Disp | Where / reason |
|---|---|---|
| Standalone image/audio/video/ingest commands with examples | H | Each command's `--help` and `schema cli`. |
| `credits`, `usage`, `log`, `validate`, `schema` | G | `guide cost.track_actuals`. |
| `verify run`, `verify scaffold` | D | Contributor tooling, not part of producing a video; `parallax verify --help`. |
| `models list`/`show` | G | `guide models`. |

## references/motion-graphics.md

| Content | Disp | Where / reason |
|---|---|---|
| When to use a motion graphic; `scene_type: motion_graphic` | G | `motion_graphic.summary`, `design_rules`. |
| Parallax accepts a rendered local clip, does not generate Remotion | G | `motion_graphic.summary`, `renderer`. |
| Author and review steps (poster, timing sketch, measured boundaries) | G | `motion_graphic.workflow`. |
| Motion design (early entrance, camera drift, caption band, safe areas) | G | `motion_graphic.design_rules`. |
| Output contract (poster, silent MP4, source, sampled frames) | G | `motion_graphic.output_contract`. |
| Brief example with `clip_path` | G | `motion_graphic.example_scene`. |
| Missing clip blocks production, no fall-through to paid generation | A | `motion_graphic_clip_missing` advisory, `blocked_by`, `plan`/`produce` errors; `motion_graphic.contract`. |
| Stills-only batch before graphics exist | G | `produce_loop.stills_only`. |
| Reassemble with approved audio and word locks | G | `motion_graphic.workflow`, `asset_locking.audio_locks`. |
| "Keep workflow here, compositions in `graphics/`" | G | `motion_graphic.output_contract` (convention only; `clip_path` may be any project path). |
| Use Remotion guidance when installed | G/A | `motion_graphic.renderer`, `motion_graphic.workflow`, `_missing_clip_advisory`. |

## references/voiceover-broll.md

| Content | Disp | Where / reason |
|---|---|---|
| Narrator tells story, characters participate | G | `voiceover_broll.summary`, `scene_rules`. |
| Concrete action per shot; reject passive poses; vary shot size | G | `scene_rules`. |
| Whole-sequence audit; action in still and motion prompt; mouths at rest | G | `scene_rules`. |
| Opening review of first 12s; pacing advisories; `storyboard --timing` | A | `opening_*` advisories; `voiceover_broll.opening_review`. |
| Speech within 100ms; `trim-leading-quiet` | A | `opening_delayed_first_word` remedy; `narration_commands`. |
| One reference per still; continuity frame only deliberately | G | `scene_rules`. |
| Show the app, not a character looking at a phone | G | `scene_rules`. |
| Narration QA (raw is not raw, ASR hides clipped phonemes) | G | `narration_qa`. |
| Hard-cut timing, frame-rounding check, overrides | G | `cuts`. |
| Cap pauses: `cap-sentence-silence` command and flags | H | `audio cap-sentence-silence --help`; `narration_commands`, `narration_edit_chain`. |
| "Competitor calibration is future work" | D | Roadmap note, not behavior. |
| Replacing a shot: keep caption safe area, refresh previews | D | `storyboard` regenerates the package; caption band covered in `motion_graphic.design_rules`. |

## Facts verified against code, corrections to the skill

- No `-y`/`--yes`: `src/parallax/cli/_produce.py`.
- Plan from brief lands in `parallax/scratch/plan.yaml`: `src/parallax/planner.py`.
- `produce` locks `still_path`/`clip_path`: `src/parallax/stages.py` `_lock_field_in_plan`; audio only via `voice_postprocess`: `stages.py` `_lock_plan_fields`.
- Default `video_model` is `draft`: `src/parallax/plan.py`.
- Dry runs write no locks into plan.yaml (`stages.py` `_lock_field_in_plan`, `_lock_plan_fields` return before the disk write in test mode). Earlier the renamed stubs (`scene_NN_still.png`) were reused by real runs and under-reported the cost estimate.
- Resolution when unset: largest `clip_path` probed, else the aspect default (`settings.py` `_infer_project_resolution`).
- `produce` needs ffmpeg and ffprobe on PATH: checked up front in `produce.py`.
