# DEV_LOG

Ground-up rewrite of the Parallax CLI. Newest-first. Captures intentional decisions, gotchas, and deferrals that git history and code alone will not preserve.

## 2026-05-04 — [DECISION] Synced main refactors into alpha directly (no PR)
Five internal refactors (#40 OpenRouter split, #42 typed Plan models, #44 stage helpers, #45 JSON-string API removal, #46 whisper backend consolidation) were mistakenly merged to `main` instead of going through the alpha PR cycle. Merged into alpha directly via `git merge origin/main`. Conflicts in `stages.py` and `assembly.py` were resolved by keeping alpha's architecture (`PipelineState`/`SceneRuntime` dataclasses, 3-arg stages) since main had drifted to an incompatible calling convention. The `_obj` wrapper variants from #45 were added on top. 401 tests pass; verify suite smoke test passes. Bumped to v0.4.18.
**Why:** Process error — parallel agent dispatch used `isolation: worktree` but PRs targeted `main` instead of `alpha`. Accepted rather than reverting because all changes are pure refactors with no CLI surface change.

## 2026-05-04 — [FIX] Plan asset locking is now explicit and non-silent (PR #50 → alpha)
`_lock_field_in_plan` in `stages.py` no longer swallows exceptions with a bare `except Exception: pass`. YAML write failures now emit a `plan.lock.error` event to the runlog at ERROR level and raise `RuntimeError`, so a failed lock surfaces immediately instead of the run silently proceeding as if the still/clip path was persisted. Path normalization behavior is unchanged (in-folder → relative, outside → absolute). Five new tests in `tests/test_plan_locking.py` cover write failure with runlog event, both path cases, and the no-active-run edge case.
**Breaks if:** a produce run whose `plan.yaml` write fails does not raise and does not emit a `plan.lock.error` runlog event; or `still_path` / `clip_path` fields are missing from `plan.yaml` after a successful run.

## 2026-05-04 — [CHANGED] run_plan returns structured ProductionResult (PR #49 → alpha)
`run_plan` in `produce.py` now returns a `ProductionResult` dataclass (`status`, `run_id`, `output_dir`, `final_video`, `stills_dir`, `cost_usd`, `error`) instead of a raw `int`. CLI layer in `cli/_produce.py` owns printing and exit-code conversion. `verify_suite.run_case` now reads `output_dir`, `cost_usd`, and `run_id` directly from the result — eliminating the brittle `parallax/output/v*/` filesystem scan and `cost.json` parse that previously coupled the test runner to storage layout.
**Breaks if:** `verify suite` fails to locate `out_dir` for a test case (would surface as "produce succeeded but output_dir is None"); or `parallax produce` no longer prints "✓ <path>" on success.

## 2026-05-03 — [CHANGED] Typed PipelineState for produce stages (PR #48 → alpha)
Replace the untyped `plan["_runtime"]` blackboard with `PipelineState` and `SceneRuntime` dataclasses. Stage signatures change from `(plan, settings)` to `(plan, settings, state: PipelineState)`; `produce.run_plan` initialises state and threads it through the loop. Also fixes a latent bug: `video_references` on plan scenes was never copied into the runtime scene dict, so it was silently dropped by `stage_animate`. The `_scene_to_dict` helper filters `None` fields when serialising `SceneRuntime` to JSON so downstream consumers (align_scenes, ken_burns_assemble) see the same sparse dicts they always received.
**Breaks if:** `plan["_runtime"]` appears anywhere outside tests after a produce run; a `video_references` field on a plan scene is ignored by `stage_animate`; or `stage_scan` / `stage_stills` / any other stage raises `AttributeError` on a valid state field.

## 2026-05-03 — [CHANGED] Scene-to-scene transitions via ffmpeg xfade (PR #37 → alpha)
Adds `default_transition` / `default_transition_duration_s` at the plan level and per-scene `transition` / `transition_duration_s` overrides. When any transition is set, `ken_burns_assemble` replaces the `-f concat` pipe with a `filter_complex` xfade chain; offset math accounts for cumulative overlap so timing stays tight. Hard-cut default is unchanged — zero breaking change. Supported transitions: `fade`, `fadeblack`, `fadewhite`, `dissolve`, `pixelize`, `wipeleft`, `wiperight`, `wipeup`, `wipedown`, `hlslice`, `hrslice`, `vuslice`, `vdslice`. Unknown names raise at assembly time with a clear error listing valid options. xfade is video-only; audio path is untouched.
**Breaks if:** a plan with `default_transition: dissolve` and 2+ scenes produces an mp4 with hard cuts (no visible dissolve), or fails with an ffmpeg `filter_complex` error, or `Plan.from_yaml()` raises "extra fields not permitted" on the new fields.

## 2026-04-29 — [CHANGED] `stage_stills` threads `size` into `generate_image` (PR #22 → main)
`stage_stills` now passes `size=settings.resolution` when calling `openrouter.generate_image`. Previously it only passed `aspect_ratio`, which fell back to the aspect-derived default (`9:16` → `1080x1920`) inside the test-mode shim regardless of the project's actual resolution. As a result, verify-suite cases at non-default resolutions (e.g. `720x1280`) could not assert `stages.stills.resolution`. Real-mode was already correct because the underlying provider call resolves size from the same plan field through a different path. Re-enabled the `stages.stills.resolution: 720x1280` assertion in `tests/integration/res-720x1280/expected.yaml` and removed the workaround comment. The original [FUTURE] entry below ("`parallax.tools.generate_image` drops `size` arg") was misleading — there is no `tools.py` module; the leak was in the `stage_stills` call site, not a separate shim.
**Breaks if:** `PARALLAX_TEST_MODE=1 uv run parallax verify suite tests/integration/res-720x1280/` does not print `[PASS] res-720x1280` and exit 0; or generated stills under that case are 1080x1920 rather than 720x1280.

## 2026-04-29 — [CHANGED] Per-stage `_log()` lines mirror into runlog (PR #21 → main)
`_log()` in `src/parallax/stages.py` now dual-emits: the existing `settings.events("log", {"msg": msg})` stdout path is unchanged, and the same `msg` is additionally written to the active runlog as a `stage.log` event via `runlog.event("stage.log", msg=msg)`. This unblocks `verify_suite` `run_log.must_contain` assertions on stage-level activity (`align_scenes`, `ken_burns_assemble`, etc.) — previously the runlog only carried `run.start` / `plan.loaded` / openrouter calls / `run.end`. Mirror placed at `_log()` rather than inside the `Settings.events` default emitter so the behavior is independent of which emitter callers inject (verify-suite installs its own). No new event taxonomy — that's still deferred.
**Breaks if:** a `PARALLAX_TEST_MODE=1 parallax produce` run produces zero `"event": "stage.log"` lines in `<output_dir>/run.log`, or `_log()` is called with an active run and the runlog file lacks a matching `stage.log` line for that message.

## 2026-04-29 — [CHANGED] CLI prints help when a command group is invoked with no subcommand (PR #20 → main)
Subparsers no longer error with `the following arguments are required` when called bare. A single tree-walk helper (`_enable_help_on_empty`) flips `required=False` on every subparsers action and stamps each parent's `print_help` into `args._help_on_empty`; leaf parsers clear the inherited default so concrete subcommands run normally. Adding new subgroups now gets this behavior for free — no hardcoded list to maintain. Also fixed two latent `%` escapes in audio help strings (`<pct%>` and `30%`) that only surfaced once those help texts started rendering.
**Breaks if:** `parallax`, `parallax audio`, `parallax models`, `parallax video`, `parallax verify`, or `parallax completions` (each invoked bare) does not print its help and exit 0; or `parallax audio --help` raises `ValueError: unsupported format character`.

## 2026-04-29 — [CHANGED] WORK BLOCK: Stabilization round-out (PRs #13/#14/#15 → main)
All three phases merged. `audio.speedup` + `parallax audio speed` + `stage_speed_adjust` lifted out of voiceover; `parallax verify {suite,init}` subgroup live; skill extracted to `~/Documents/GitHub/parallax-skill` with `~/.claude/skills/parallax` repointed.
**Breaks if:** `voiceover.generate_voiceover` accepts a `speed` kwarg; `parallax verify-suite` resolves; `~/.claude/skills/parallax` resolves into parallax-v0; `src/parallax/skills/` exists.

## 2026-04-29 — [HISTORY] WORK BLOCK scope (original — see PRs above for shipped state)

**Goal:** Tie up the loose ends surfaced during the Layer 1 + cleanup + logging refactors so the CLI surface is internally consistent before we layer anything new on top. Three small, independent phases — each is one PR's worth of work and none block the others.

**Why this scope, why now:** Layer 1 + cleanup + logging shipped fast and surfaced a handful of "we said we'd do this later" items. None are urgent in isolation; together they're the difference between "the CLI works" and "the CLI is clean." Doing them now while the architecture is fresh in context is cheaper than rediscovering them later. Skill repo extraction in particular blocks any second consumer of the skill (a Claude Code instance on a fresh machine still pulls the skill from inside the parallax-v0 repo).

**Out of scope:**
- Narrative-parallax Layer 3 / Layer 4 work — separate repo, separate concern.
- Plan/brief schema additions beyond what each phase explicitly calls out.
- Real-API verify-suite cases — `--paid` cases stay deferred until we have a budget loop.
- Any new commands beyond what the phases below introduce (`parallax audio speed`, `parallax verify suite|init`).

### Phase 1 — Speed separation: lift `_apply_atempo` into `audio.py`  [INCOMPLETE]

**Description:** The atempo pass currently lives inside `voiceover.py` as `_apply_atempo`, gated by the plan-level `voice_speed:` field. Speed is a generic audio transform, not a voiceover-specific concern. Lift it into `src/parallax/audio.py` as `audio.speedup(in_path, out_path, rate) -> Path` (also accept a natural-English `--by 30%` form on the CLI side that translates to a numeric rate). Expose as `parallax audio speed --in <file> --out <file> --rate <multiplier>`. Add a new produce stage `stage_speed_adjust` driven by the existing plan-level `voice_speed:` (and per-scene override) that calls the same function. `voiceover.generate_voiceover` drops the `speed` arg — voiceover becomes pure TTS. The same shape `parallax audio speed` would generalize to any in-pipeline audio transform later (cap-pauses, normalize, etc. are already CLI-first; speed joins the pattern).

**Acceptance:**
- `parallax audio speed --in a.wav --out b.wav --rate 1.3` produces a 1.3× wav.
- `parallax audio speed --in a.wav --out b.wav --by 30%` produces the same.
- A produce run with `voice_speed: 1.2` in the plan still ships an mp4 whose voiceover is sped up — but the work happens in `stage_speed_adjust`, not inside voiceover.
- `voiceover.generate_voiceover()` no longer accepts a `speed` kwarg.
- `audio.speedup()` is unit-tested in isolation (rate=1.0 = identity, rate=1.5 shortens duration by 33%, ffmpeg failure raises with a clear message).

**Self-verification (subagent):** `uv run pytest -q` green; `parallax audio speed` round-trips a fixture wav at three rates; `PARALLAX_TEST_MODE=1 parallax produce` with `voice_speed: 1.2` produces an mp4 whose voiceover.wav is shorter than the natural-pace baseline.

**Human verification steps:** Listen to a real-API run with `voice_speed: 0.9` (slower) and `voice_speed: 1.3` (faster) and confirm the audio actually changes.

**Breaks if:** `voiceover.generate_voiceover` still accepts a `speed` kwarg, or a plan with `voice_speed: 1.5` produces an mp4 the same length as a plan with `voice_speed: 1.0`.

### Phase 2 — `parallax verify suite|init` subgroup rename  [INCOMPLETE]

**Description:** `verify-suite` and `verify-init` are obviously paired but currently flat-named at the top level. Move them under a `verify` subgroup: `parallax verify suite <dir>` and `parallax verify init <target>`. Drop the dashed forms entirely (single-user, one release; no shim). The implementation lives in `verify_suite.py` already; only the argparse wiring changes.

**Acceptance:**
- `parallax verify --help` lists `suite` and `init` as subcommands.
- `parallax verify suite tests/fixtures/verify_suite_smoke/` runs the smoke suite.
- `parallax verify init <target> --from <existing>` scaffolds a new case.
- `parallax verify-suite` and `parallax verify-init` are gone (not registered, not aliased).
- Existing `tests/test_verify_*.py` updated for the new invocation surface.

**Breaks if:** `parallax verify-suite tests/fixtures/verify_suite_smoke/` still resolves, or `parallax verify suite` is missing.

### Phase 3 — Layer 2.5: Extract the parallax skill into its own repo  [INCOMPLETE]

**Description:** Today the canonical generic Parallax skill ships inside this repo at `src/parallax/skills/parallax-cli.skill.md` (≤3K, accurate against the current CLI). For any other Claude Code session on any other machine to use it, that machine has to either clone parallax-v0 just for the skill or symlink into the parallax-v0 worktree. Extract the skill into a standalone `parallax-skill` repo so it can stand alone:

1. `git init ~/Documents/GitHub/parallax-skill`. Add the skill content as the new repo's main file (filename + frontmatter to match Claude Code skill conventions).
2. Repoint the user's `~/.claude/skills/parallax` symlink from `parallax-v0/src/parallax/skills/parallax-cli.skill.md` (or wherever it currently points) to the new repo.
3. In a follow-up commit on `parallax-v0`, delete `src/parallax/skills/` and the `[tool.hatch.build.targets.wheel].artifacts` entry that ships the skill in the wheel. Update `pyproject.toml` accordingly.
4. Add a one-line note in this repo's README pointing at `parallax-skill` so anyone reading parallax-v0 knows where the agent guide actually lives.

**Acceptance:**
- `~/.claude/skills/parallax` resolves to the new `parallax-skill` repo (verifiable via `ls -la`).
- `parallax-v0` no longer contains `src/parallax/skills/`.
- The new repo has its own minimal README pointing at parallax-v0.
- `parallax produce` etc. still work (the CLI never depended on the skill at runtime).

**Breaks if:** `~/.claude/skills/parallax` resolves to a path inside `parallax-v0`, or the parallax-v0 wheel still ships the skill.markdown file.

---

## 2026-04-29 — [CHANGED] Logging overhaul — output-dir runlog, runs.ndjson index, `parallax log`
- Run log moved from `~/.parallax/logs/<run_id>.log` to `<output_dir>/run.log`
  — single canonical location, no symlinks, no duplication. Pre-output_dir
  events are buffered in memory and flushed at stage_scan.
- New `~/.parallax/runs.ndjson` index: one append-only line per run with
  run_id / short_id / output_dir / started / ended / status / cost_usd /
  scene_count. Powers `parallax log latest` / `log list`.
- DEBUG events emitted at every stage entry/exit and every ffmpeg subprocess
  invocation. Level field already on every event; the display layer filters
  by --level.
- Output mp4 filename now `<folder>-v<N>-<short_id>.mp4` so the run is
  traceable from the artifact alone.
- `parallax tail` replaced by `parallax log <spec> [--level] [--summary]
  [--follow]` and `parallax log list`. Summary view is the default —
  operator-readable digest with stage timings and provider call list.
- Cost rollup bug fixed: `run.end` now sums `usage.run_total(run_id)` instead
  of reporting $0.
**Breaks if:** `parallax tail` still resolves; `~/.parallax/logs/` is created
on a new run; `parallax log latest` doesn't find the most recent run; the
final mp4 filename lacks the `-<short_id>` suffix.

## 2026-04-29 — [CHANGED] Cleanup pass — Plan model, field renames, provider consolidation
- Pydantic `Plan` model in `src/parallax/plan.py` — strict (extra="forbid"),
  single load point in `produce.run_plan`. Mirrors `Brief`.
- Renamed `plan.model`→`image_model`, `plan.animate_model`→`video_model`;
  added `plan.voice_model` (default `tts-mini`). Per-scene overrides for
  all three. Old field names rejected at load with "rename to <new>" message.
- `openrouter_tts.py` folded into `openrouter.py`. Hoisted single shared
  client config (`_BASE`, `_BASE_HEADERS`, `_post`, `_stream_post`) so
  endpoint URL + headers live in exactly one place.
- Deleted `tools.py` and `tools_video.py` compat shims. Test imports now
  point directly at the real modules.
- `install.sh` rewritten for the OpenRouter-only era — prompts for
  OPENROUTER_API_KEY, drops the FAL/Anthropic backend logic.
**Breaks if:** plan.yaml using `model:` or `animate_model:` parses without
error; `parallax models list` fails; `from parallax.tools import generate_image`
or `from parallax.tools_video import ...` resolves; `openrouter_tts.py` exists.

## 2026-04-29 — [CHANGED] Phase 1.7 — CLI wiring (plan / ingest / --brief)
Added `parallax plan` and `parallax ingest` subcommands; `--brief` on `parallax produce` runs the planner first then produces from the materialized plan. `parallax test-scene` collapsed into `parallax produce --scene <N>`. Aspirational V2 commands (`generate`, `script`, `edit`, `compose`, `setup`, `status`, `web`, `project`, `publish`) intentionally NOT added — they have no implementation and stub commands create dead surface area. Help-text order matches the V2 namespace.
**Breaks if:** `parallax test-scene` is still a registered subcommand, or `parallax plan --folder X` succeeds when X has no brief.yaml, or `parallax produce --brief` and `parallax produce --plan` can both be passed simultaneously without an error.

## 2026-04-29 — [CHANGED] Layer 2 — generic SKILL.md slimmed to ≤3K
Reduced SKILL.md from ~41K to ≤3K. Stripped narrative-specific content (concepts.json, frame.io, sheets, drive, brand presets, narrative orchestrator workflows) and obsolete provider content (ElevenLabs, fal, Aurora avatar gen). The slim skill covers: brief→plan→produce loop, plan-yaml locking conventions, model alias tiers, aspect ratio knob, response terseness rule, single env var. The packaged copy at `src/parallax/skills/parallax-cli.skill.md` mirrors the same content.
**Breaks if:** SKILL.md grows back over 3K, mentions any forbidden term (frame.io, concepts.json, etc.), or stops naming brief.yaml/plan.yaml as the iteration artifacts.

## 2026-04-29 — [CHANGED] Phase 1.3 — aspect ratio first-class
Added `aspect:` to Settings + plan.yaml (top-level + per-scene). `--aspect` flag on `produce` / `test-scene`. Resolution now derives from aspect when not explicitly set; mapping table at `_ASPECT_TO_RESOLUTION` covers 9:16, 16:9, 1:1, 4:3, 3:4. Pulled the aspect carrier out of `spec.portrait_args` — image and video calls now take `aspect_ratio` directly from the call site, and the catalog loader no longer injects `portrait_args`. Stern-prefix prompt for stills generation derives from `settings.aspect` instead of being hardcoded "9:16 vertical portrait".
**Breaks if:** producing with `--aspect 16:9` results in a portrait-shaped final.mp4, or any image-gen prompt mentions "9:16" when the chosen aspect is something else, or `parallax produce` on a plan with no `aspect:` field stops defaulting to 9:16.

## 2026-04-29 — [CHANGED] Phase 1.6 — `parallax ingest` core (ingest.py)
Added `src/parallax/ingest.py` exposing `ingest(target, ..., visual=False, estimate=False) -> IngestResult`. Walks a clip/dir, probes duration, parallel-runs `audio.transcribe_words` per clip, and emits a single `index.json` with per-clip words + duration. `--estimate` short-circuits to a cost report. `--visual` is a stub raising NotImplementedError until the Gemini Vision path lands. CLI subcommand wiring deferred to the V2-namespace pass.
**Breaks if:** ingest on a directory of clips writes per-clip JSON files instead of one aggregated index.json, or a single-clip ingest writes its index anywhere other than `<file>.index.json`.

## 2026-04-29 — [CHANGED] Phase 1.5 — `parallax plan` core (planner.py)
Added `src/parallax/planner.py` exposing `plan_from_brief(brief, folder, ...) -> PlanResult`. Pure deterministic translation: validates provided assets, materializes `Brief.to_plan_skeleton()`, adds planner-only fields (`model`, `caption_style`, `character_image`), and writes plan.yaml. Missing-asset path writes `questions.yaml` and returns `ok=False`. CLI subcommand wiring deferred to the V2-namespace pass; this is the importable core.
**Breaks if:** running the planner against a brief whose provided assets all exist still produces a `questions.yaml`, or a fully-resolved plan.yaml is missing the brief's `aspect` / `voice` / `scenes` content verbatim.

---

## Work block & phase template

Larger pieces of work are scoped as **work blocks** at the top of this file. Each block has a name, a date, a master `[INCOMPLETE]` / `[COMPLETE]` flag, and one or more **phases**. Each phase has its own `[INCOMPLETE]` / `[COMPLETE]` flag, a detailed description, a validation section, and an updates log appended as the phase progresses.

### Why blocks and phases

A single PR is a unit of code. A single block is a unit of *intent* — usually too big for one PR, often too big for one session. Phases inside a block are sequenced PR-sized deliverables. The structure makes it easy for a future agent (or human) to (a) see what's in flight, (b) resume mid-block without losing context, and (c) verify each phase landed cleanly before the block closes.

### Flow per block

1. **Scope.** Head agent + user agree on the block's name, master flag, and phase breakdown. Each phase is detailed thoroughly (acceptance criteria, validation steps, what's *not* in scope). Block + phases all start `[INCOMPLETE]`. Any open questions are surfaced as WHY-prompts to the user before the block locks; the goal is to favor structural rewrites over patchwork unless we have a deliberate reason to patch.
2. **Execute one phase at a time.** Head agent spawns a focused subagent for the phase. The subagent self-verifies aggressively before reporting back — runs `uv run pytest`, runs the affected CLI command end-to-end, uses Playwright when there's a UI, etc. The bar: never surface broken code to the user.
3. **Surface to user.** Subagent returns a list of human-side verification steps. The user runs them, then either approves with "move on", returns notes/critiques, or asks for a follow-up phase to be added. Notes that warrant changes loop back into the subagent.
4. **Close the phase.** Once the user signs off, head agent flips the phase flag to `[COMPLETE]`, appends a final update line, and confirms the next phase is ready.
5. **Close the block.** When all phases are `[COMPLETE]`, the master block flag flips and the block migrates from "WORK BLOCK" to a normal `[CHANGED]` / `[DECISION]` entry summarizing what shipped and the `Breaks if:` line for the assembled deliverable.

### Template

```markdown
## YYYY-MM-DD — [INCOMPLETE] WORK BLOCK: <Block Name>

**Goal:** <one paragraph on what this block delivers and why it matters now>

**Why this scope, why now:** <answers to the WHY-prompts asked during scoping; locks in the rewrite-vs-patch posture>

**Out of scope:** <bulleted list of things explicitly not being addressed; protects the block from creep>

### Phase 1 — <Phase Name>  [INCOMPLETE]

**Description:** <what this phase delivers, in detail; should read like a brief>

**Acceptance:**
- <observable criterion 1>
- <observable criterion 2>
- ...

**Self-verification (subagent):** <commands the subagent runs to confirm the work before returning>

**Human verification steps:** <what the user runs / observes after the subagent reports done>

**Updates:**
- YYYY-MM-DD — kicked off subagent (commit <sha>)
- YYYY-MM-DD — subagent reported done; awaiting user verification
- YYYY-MM-DD — user signed off ("move on"); flag flipped to [COMPLETE]

### Phase 2 — <Phase Name>  [INCOMPLETE]

...
```

When a phase or block flips to `[COMPLETE]`, the flag is updated *in place* — the entry stays where it is until the whole block closes, at which point it migrates to the normal newest-first stream as a regular `[CHANGED]` entry.

---

## 2026-04-29 — [CHANGED] Phase 1.2 — single-provider consolidation (OpenRouter only)
Removed every fal_client and elevenlabs path. TTS now routes Gemini Flash Preview TTS via OpenRouter's `/api/v1/audio/speech` endpoint (rewrote `gemini_tts.py`). Video animation routes through `openrouter.generate_video` instead of `fal_client.subscribe`. Avatar generation deleted (no OpenRouter equivalent for fal-ai/creatify/aurora); chromakey + burn stages remain. Single env var: OPENROUTER_API_KEY.
**Breaks if:** any TTS call attempts to hit a Google or ElevenLabs endpoint, any video clip arrives via fal, or the CLI rejects a real-mode run that has only OPENROUTER_API_KEY in the environment.

## 2026-04-28 — [FIX] `crop_to_aspect` deletes source after writing cropped variant
`stills.crop_to_aspect` left the pandoc-extracted original in place after writing the `_aWxH` variant, so downstream readers of the concept's `media/` dir saw both files and the agent passed every reference twice (original + cropped) into the next image-edit call. Added `src.unlink(missing_ok=True)` after the save (and after the cached-out early-return), mirroring the pattern already used in `normalize_aspect`. Single source of truth for cleanup is the function that creates the variant; no need for periodic dedup sweeps in narrative-parallax.
**Breaks if:** a concept folder's `media/` ends up containing both `imageN.png` and `imageN_a720x1280.png` after a stills_pending tick — should only ever see the cropped variant.

## 2026-04-28 — [FIX] Retry transient network errors in `_with_fallback` before falling through

A single SSL alert during scene 4's image generation aborted a 4-scene render in narrative-parallax run `20260428T172336Z-cebff9`, pushing the concept to `blocked_assets`. Root cause: `_with_fallback` treated *every* exception as model-level and immediately fell through to the next spec in the chain. A network blip (TLS read error, connection reset, 5xx) is the network's fault — retrying a different model on the same network is wasted spend, and worse, silently swaps visual style mid-render.

Wrapped the per-spec call in `_call_with_transient_retry`: 3 attempts with exponential backoff (1s, 2s) gated by `_is_transient_network_error` (TLS / connection / read / protocol class names + 5xx-shaped messages). Non-transient errors (validation, safety, "no images returned") raise immediately so `_with_fallback` can move on to the next model as before. `InsufficientCreditsError` still re-raises loud — wallet errors don't get retried.

**Breaks if:** a single `httpx.ReadError` / `SSLError` / `ConnectError` from `_image_real` jumps straight to the fallback spec (it should now log `openrouter.image.error` with `transient: true` and retry the same alias up to 3 times before falling through); the runlog should show repeated `attempt` numbers for the same `model_id` on a transient blip.

## 2026-04-28 — [FUTURE] `parallax.tools.generate_image` drops `size` arg  [SUPERSEDED — see 2026-04-29 entry]
Phase 1.3 threaded `size` through `parallax.openrouter.generate_image` so test-mode mocks could honor requested resolution. The thin compatibility shim at `parallax.tools.generate_image` (used by `stage_stills`) was missed — it accepts no `size` param and so always lands on `render_mock_image` with `resolution="1080x1920"` regardless of the plan. Real-mode is unaffected (the underlying image-gen path goes through the openrouter dispatcher with size set elsewhere), but it means verify-suite cases at non-1080x1920 resolution can't assert `stages.stills.resolution`. The Phase 1.4 canonical case (`tests/integration/res-720x1280/`) works around this by omitting that one assertion and relying on `final.resolution` + `assemble.resolution` for the product-level guarantee. Fix is a 2-line shim update — accept `size`, forward to `_generate_image` — but it touches the `produce` path so it gets its own pass. Do this before Phase 2.1 lands so the resolution-adaptation cases can assert at every stage.

## 2026-04-28 — [CHANGED] verify-suite shipped — three schema deviations from the draft
The Phase 1.2 `expected.yaml` schema diverged from the DEV_LOG draft in three places, all surfaced during smoke-fixture iteration. Documenting them here so the schema-of-record is the one in `src/parallax/verify_suite.py` / `AGENTS.md`, not the original spec block.

1. **`stages.assemble.files_must_exist` cannot reliably target `video/ken_burns_draft.mp4`.** `stage_finalize` calls `Path(current_video).rename(out_dir / convention_name)` — when captions are skipped and no headline/avatar runs, the draft mp4 is *moved up* to `<folder>-vN.mp4` at the out_dir root and no longer exists at `video/`. Smoke fixture asserts `*.mp4` so it works for both the captions-on (draft persists) and captions-off (draft renamed) paths.
2. **`stages.voiceover.files_must_exist` uses `audio/voiceover.*` not `audio/voiceover.wav`.** The mock voiceover writes `voiceover.mp3` (silent libmp3lame), and the real Gemini path also writes `.mp3`. Only the ElevenLabs path produces `.wav`. Wildcard suffix keeps the schema backend-agnostic.
3. **`run_log.must_contain` cannot match per-stage log lines.** `_log()` in `stages.py` emits via `Settings.events` (default → stdout `==>`), not `runlog.event()`. The runlog JSONL only contains `run.start`, `plan.loaded`, external-call records, and `run.end`. Smoke fixture asserts `plan.loaded` / `run.end` instead of `align_scenes` / `ken_burns_assemble`. Worth wiring `_log()` through `runlog.event()` later (see [FUTURE] below) so stage tracing lands in both channels — but that's an architectural change to `Settings.events`, out of scope for Phase 1.2.

**Breaks if:** `parallax verify-suite tests/fixtures/verify_suite_smoke/` does not print `[PASS] basic` and exit 0 in `PARALLAX_TEST_MODE=1`.

## 2026-04-28 — [FUTURE] Lower-level modules still read `PARALLAX_TEST_MODE` directly
Stages now thread `settings.mode` and never touch `os.environ`, but the utility modules they call (`assembly`, `project`, `voiceover`, `openrouter`, `shim`) still resolve test-mode via `is_test_mode()` from `shim.py`. That's deliberately out of scope for Phase 1.1 — those signature changes would cascade through every stage call site and contradict "stages wrap existing module functions, they don't redesign them". When the verify-suite needs to alternate REAL/TEST in one process at the utility level (e.g. testing voiceover.real-vs-mock paths in a single pytest run), thread `settings` through their public APIs and drop `is_test_mode()`. Worth revisiting if/when verify-suite cases need that capability.

## 2026-04-28 — [FUTURE] Default `_log` emitter still prints — verify-suite needs a structured event taxonomy
`Settings.events` is wired but the default emitter just prints `==> {msg}`. Stages call `_log(settings, msg)` everywhere with human-readable strings; the structured event surface (`stage.stills.start`, `stage.stills.scene.lock`, `stage.assemble.draft.written`, etc.) isn't defined yet. Phase 1.2 (`expected.yaml` schema + verify-suite) will need that taxonomy to assert per-stage activity. Land in lockstep with the schema design — defining events in isolation of what verify-suite asserts would be premature.

## 2026-04-28 — [INCOMPLETE] WORK BLOCK: Parallax CLI Integration Test Suite

**Goal:** Build an integration-test harness that lets us validate the entire `parallax produce` pipeline at multiple resolutions, with multiple model combinations, and through every conditional branch (locked vs unlocked assets, captions on/off, headline on/off, avatar pipeline, voice routing). Most cases run free in `PARALLAX_TEST_MODE=1`; a small smoke set runs paid against real APIs. The deliverable is the runner + the reference matrix of cases — once it's in place, every change to the CLI gets validated against the matrix instead of by manual eyeballing of a single demo project.

**Why this scope, why now:**
- The CLI is approaching feature-complete; integration tests are the last big investment before attention pivots to the `narrative-parallax` agent layer. The contract this block locks is the contract every future change has to honor.
- The expanded `expected.yaml` schema (final + per-stage + per-asset + contiguity + manifest + run-log) is rich enough to catch every regression class we've shipped fixes for in the last 24 hours (aspect stretch, voiceover-tail clip, scene-cover invariants, hardcoded resolutions). Locking it thinly and reving later would force two test-rewrite passes; locking it rich now means new fields are additive only.
- Decomposing `produce.py` into staged callables is folded *into* this block (Phase 1.1), not deferred. Doing it before the matrix means every test in Block 2 can target a single stage — dramatically faster (no full-pipeline re-run per branch) and dramatically more diagnostic (failure pinpoints to a stage, not a 700-line trace). Holding the line was a speed hedge; the user's instruction was "rewire now if it sets us up for success", which it does.

**Out of scope:**
- Testing the `narrative-parallax` agent layer (separate work block — parallax CLI must be solid before we sit on top of it).
- CI integration (GitHub Actions, etc.) — this block delivers a local runner; CI wiring is a follow-up once the matrix is stable.
- Visual quality assertions (pixel comparison, OCR of captions). These tests verify dimensions, durations, file existence, contiguity invariants — not aesthetics.
- Refactoring beyond `produce.py` decomposition. Other modules that get touched during the rewire stay as-is; refactor smells captured as `[FUTURE]` entries in DEV_LOG.

### Block 1 — Test Harness Foundation  [COMPLETE]

The scaffolding everything else builds on. After Block 1, you can write a single test case folder by hand and run it with `parallax verify-suite <folder>`. Block 1 also locks the rewire of `produce.py` into staged callables so the test matrix can target individual stages instead of always running the whole pipeline.

#### Phase 1.1 — Decompose `produce.py` into staged callables (full rewire)  [COMPLETE]

**Description:** Six coordinated sub-deliverables that together make the pipeline testable stage-by-stage. Doing them as one phase rather than six because they're load-bearing for each other (Settings depends on parse_resolution; stage decomposition depends on Settings + ProductionMode + logger + cost-session). One subagent run, ~18 commits, full test suite green between each. The 181-test characterization safety net is the spotter; the lumawrap end-to-end (locked assets → free) is the second spotter.

The rationale for folding all six into one phase comes from the audit on 2026-04-28 that surfaced testability blockers around shared global state (`is_test_mode()` reads, module-global `_log`, global `_usage.record`). Splitting any of these to a follow-up phase would force two test-rewrite passes — once against a half-rewired surface and once against the final shape.

**Sub-deliverables (in order; each its own commit batch):**

1. **`Settings` dataclass + `parse_resolution` helper.** `run_plan` reads ~30 plan keys at the top before doing work, then passes them as positional args downstream. Replace with a frozen `Settings` dataclass returned by `resolve_settings(plan, folder) -> Settings`, threaded through every stage. `parse_resolution(s) -> tuple[int, int]` lives in `ffmpeg_utils`; replaces 6 duplicated `resolution.split("x")` sites.

2. **`ProductionMode` enum threaded through stages.** `is_test_mode()` is called from 6 modules. Each reads `os.environ["PARALLAX_TEST_MODE"]` inline — two cases with different test-mode semantics can't run in the same process. Add `class ProductionMode(Enum): REAL, TEST` and `Settings.mode: ProductionMode`. Stages take it via `settings`. The env var resolves once at `run_plan` / `verify-suite` entry, never inside a stage.

3. **Logger / event-emitter injection.** `_log` is a module-global wrapper that prints + emits runlog events. Stages can't be tested in isolation while they call into a module-global. Add `Settings.events: Callable[[str, dict], None] | None`; default → existing `_log`. Verify-suite installs its own callback to capture per-stage activity.

4. **Cost-session aggregator.** `_usage.record(...)` writes to a global usage log; `cost_usd_max` assertions in `expected.yaml` need a clean per-run total. Add a `usage_session()` context manager (or `UsageSession` field on Settings); aggregates spend, exposes `session.total_cost_usd`. Existing global usage log keeps working as the default sink.

5. **Decompose `run_plan` into ~10 staged callables.** With Settings + ProductionMode + events + UsageSession in place, the rewrite is mechanical. Stages: `stage_stills`, `stage_animate`, `stage_voiceover`, `stage_align`, `stage_manifest`, `stage_assemble`, `stage_captions`, `stage_titles`, `stage_headline`, `stage_avatar`, `stage_finalize`. Each takes `(plan, settings) -> updated_plan` and mutates disk. `run_plan` becomes "resolve_settings → for stage in STAGES: plan = stage(plan, settings)". One commit per extracted stage, full pytest suite + lumawrap-locked produce smoke between each.

6. **Migrate `produce.py` imports off `tools_video` shim.** `produce.py` still imports a lot from `tools_video` (the 72-line compat shim from Block 4 Phase 2). After decomposition, those imports should reference the actual extracted modules (`assembly`, `captions.burn`, `headline`, etc.). Pure rename; no behaviour change.

**Acceptance:**
- `produce.py` ≤200 lines (orchestrator + glue only).
- `Settings` dataclass exported, exhaustively typed, frozen.
- Zero `os.environ` reads inside any stage (only at `run_plan` / `verify-suite` entry).
- Zero references to module-global `_log` inside any stage.
- `uv run pytest -q` stays at 181 passing throughout the rewire (every commit).
- `uv run parallax produce --folder ~/Documents/parallax-demo/lumawrap --plan .../scratch/plan.yaml` produces a final mp4 with identical resolution, duration ±0.05s, scene count, and manifest keys before vs after the rewire.
- Each stage has a one-line docstring + a `Breaks if:` line.

**Self-verification (subagent):**
- `uv run pytest -q` after every commit. Hard fail-fast on the first regression.
- Pre-rewire baseline: capture `ffprobe` output of `lumawrap-v1.mp4` + `manifest.yaml` to `/tmp/pre_rewire_baseline.txt`.
- Post-rewire: re-run `parallax produce` against the same locked-asset plan. Diff against baseline. Any divergence (other than version-dir bump) blocks reporting done.
- Spot-test `Settings` immutability: attempt to mutate a field, expect `FrozenInstanceError`.
- Spot-test mode threading: invoke `stage_stills` with `settings.mode = ProductionMode.TEST` from a test, assert no network call.

**Human verification steps:**
1. From repo root: `uv run pytest -q` — expect `181 passed`.
2. From repo root: `wc -l src/parallax/produce.py` — expect ≤200 (currently 187).
3. From repo root: `grep -n "os.environ\|os\.getenv" src/parallax/stages.py src/parallax/produce.py` — expect no matches.
4. From repo root: `grep -n "tools_video" src/parallax/produce.py src/parallax/stages.py` — expect no matches.
5. Settings immutability spot-check (one-liner): `uv run python -c "from parallax.settings import resolve_settings; from pathlib import Path; from dataclasses import FrozenInstanceError; s = resolve_settings({'scenes':[{'index':0}]}, Path('/tmp'), Path('/tmp/p.yaml')); s.model='cheat'"` — expect `FrozenInstanceError`.
6. Mode threading spot-check: `PARALLAX_TEST_MODE=1 uv run python -c "from parallax.settings import resolve_settings, ProductionMode; from pathlib import Path; s=resolve_settings({'scenes':[{'index':0}]}, Path('/tmp'), Path('/tmp/p.yaml')); assert s.mode == ProductionMode.TEST; print('OK')"` — expect `OK`.
7. Lumawrap end-to-end smoke (free, ~15s): `uv run parallax produce --folder ~/Documents/parallax-demo/lumawrap --plan ~/Documents/parallax-demo/lumawrap/parallax/scratch/plan.yaml` — expect a final `lumawrap-vN.mp4` printed.
8. Probe the produced mp4 against baseline: `ffprobe -v error -show_entries stream=width,height,duration,nb_frames -show_entries format=duration ~/Documents/parallax-demo/lumawrap/parallax/output/v<N>/lumawrap-v<N>.mp4` — expect `width=720`, `height=1280`, `duration=12.166667`, `nb_frames=365` for video stream and `nb_frames=286` for audio. Compare to `/tmp/pre_rewire_baseline.txt`.

**Updates:**
- 2026-04-28 — subagent ran 6 sub-deliverables in sequence; commits 1ae014e..8ef9e1b. 181 tests green between every commit. Lumawrap end-to-end produced byte-equivalent structural output (only run_id/session_id/version-dir differ). Lumawrap plan was updated once at the start of the run to add `audio_path`/`words_path` locks pointing at the existing v1 voiceover so the baseline runs free (no Gemini TTS spend) — this is now the canonical state of the lumawrap demo plan.
- 2026-04-28 — user verified all 8 steps live (181 passing, produce.py 187 lines, no env reads in stages, no tools_video imports, FrozenInstanceError on mutation, ProductionMode.TEST threaded, lumawrap-v7 structural-equivalent to baseline). Said "move on". Flag flipped to [COMPLETE].

---

**Side fixes shipped during Phase 1.1 verification (commit `989bf63`)** — surfaced by the live gloam test that exposed two real defects beyond Phase 1.1's brief:

1. **Gemini was returning landscape stills** despite `aspect_ratio: "9:16"` in the body. `stills.normalize_aspect` was silently center-cropping the landscape into portrait, discarding subject content (the lamp got cut off in the gloam v1 final). Replaced silent-crop with a hard validator: stills mismatching by >2% raise `AspectMismatchError`. `stage_stills` retries once with a sterner prompt prefix, then raises if still wrong. Plus a textual aspect cue (`"Vertical 9:16 portrait orientation, taller than wide..."`) is now prepended automatically by `_image_real` when the model spec carries `portrait_args`. Verified 5/5 reliability against gemini-3-flash with credits funded.
2. **Every OpenRouter call was 402'ing silently** because the wallet was exhausted, and `_with_fallback` was catching the 402 as a generic exception and trying alternates on the same wallet — guaranteed-fail noise. Added `InsufficientCreditsError`, pre-flight `check_credits()` at the top of `run_plan`, special-casing 402 in `_raise_for_credits_or_status`, and a `parallax credits` CLI to probe balance.

Both side fixes get filed under Phase 1.1 because they were discovered + fixed during its live verification cycle, not in a separate phase.

#### Phase 1.2 — `expected.yaml` schema + `parallax verify-suite` command  [COMPLETE]

**Description:** Define the assertion schema and ship a runner subcommand. The schema is rich enough to catch every regression class we've shipped fixes for in the last 24 hours — final-state, per-stage artifacts, per-asset aspect, contiguity, manifest contract, run-log smoke, cost guardrail. Runner takes a directory containing one or more case subfolders (each with `plan.yaml` + `expected.yaml` + optional `README.md` + optional pre-locked `assets/`), runs `produce` on each, asserts every field present in the case's `expected.yaml`, and prints `[PASS] <name> (Xs)` or `[FAIL] <name> — <field>: expected <X>, got <Y>`. Exit non-zero on any failure. `--paid` opts in to cases marked `paid: true`.

**Schema (full):**
```yaml
name: res-720x1280
description: One-line summary.
paid: false                          # default false; --paid required for true
cost_usd_max: 0.0                    # exceeded → fail (catches API leaks in test mode)

final:
  resolution: 720x1280
  duration_s: { min: 5.0, max: 12.0 }
  audio_video_diff_s_max: 0.05
  scene_count: 4

stages:                              # per-stage outputs (each block optional)
  stills:
    files_must_exist: ["stills/*.png", "stills/*.jpg"]
    aspect_within_pct_of_project: 0.5
  voiceover:
    files_must_exist: ["audio/voiceover.wav", "audio/vo_words.json"]
    word_count_min: 1
    word_total_matches_wav_within_s: 0.05
  assemble:
    files_must_exist: ["video/ken_burns_draft.mp4"]
    resolution: 720x1280              # frame size preserved through assemble
    contiguous_cover: true            # scene_0.start=0; consecutive; last.end=total
  captions:
    files_must_exist: ["video/captioned.mp4"]
    resolution: 720x1280
  headline:
    files_must_exist: ["video/final.mp4"]
    resolution: 720x1280

manifest:
  keys_required: [model, voice, resolution, scenes]
  scene_keys_required: [index, vo_text, prompt, start_s, end_s, duration_s]

run_log:
  must_not_contain: ["Traceback", "ERROR"]
  must_contain: ["align_scenes", "ken_burns_assemble"]
```

**Acceptance:**
- `parallax verify-suite --help` documents every flag and the schema.
- Empty directory → exit 0, `0 cases run`.
- Single-case run asserts every present field; missing fields are silently skipped.
- Each failure prints the exact assertion that failed with expected vs actual values.
- `--paid` flag is required to run cases marked `paid: true`; otherwise they're skipped with `[SKIP] <name> — paid (use --paid)`.

**Self-verification (subagent):** `uv run pytest -q`, hand-crafted single-case fixture in `tests/fixtures/verify_suite_smoke/`, deliberate failure injection (mutate one field) to confirm the runner reports clearly.

**Human verification steps:**

```sh
# 1. All tests green (187 + 16 new = 203):
uv run pytest -q

# 2. Help text renders the schema summary:
uv run parallax verify-suite --help

# 3. Smoke fixture passes:
PARALLAX_TEST_MODE=1 uv run parallax verify-suite tests/fixtures/verify_suite_smoke/
# → [PASS] basic (~1s)   exit 0

# 4. Deliberate-fail rendering — mutate, run, restore:
sed -i '' 's/^  resolution: 1080x1920$/  resolution: 9999x9999/' \
  tests/fixtures/verify_suite_smoke/basic/expected.yaml
PARALLAX_TEST_MODE=1 uv run parallax verify-suite tests/fixtures/verify_suite_smoke/ ; echo "exit=$?"
# → [FAIL] basic — final.resolution: expected 9999x9999, got 1080x1920 …  exit=1
git checkout -- tests/fixtures/verify_suite_smoke/basic/expected.yaml

# 5. Single-case filter:
PARALLAX_TEST_MODE=1 uv run parallax verify-suite tests/fixtures/verify_suite_smoke/ --case basic

# 6. Paid gating:
yq -i '.paid = true' tests/fixtures/verify_suite_smoke/basic/expected.yaml   # or hand-edit
PARALLAX_TEST_MODE=1 uv run parallax verify-suite tests/fixtures/verify_suite_smoke/
# → [SKIP] basic — paid (use --paid)   exit 0
PARALLAX_TEST_MODE=1 uv run parallax verify-suite tests/fixtures/verify_suite_smoke/ --paid
# → [PASS] basic
git checkout -- tests/fixtures/verify_suite_smoke/basic/expected.yaml
```

**Updates:**
- 2026-04-28 — Shipped `src/parallax/verify_suite.py` (`load_expected`, `run_case`, `run_suite`, `cli_run`), `parallax verify-suite` subcommand, `tests/fixtures/verify_suite_smoke/basic/`, and 16 pytest cases. 203/203 tests green. Schema mostly matches the draft, with three deliberate adjustments noted in the [GOTCHA]/[CHANGED] entry at the top of this log.
- 2026-04-28 — user verified all 4 steps live (203 passing, --help renders schema summary, smoke fixture [PASS] basic exit 0, deliberate-fail [FAIL] basic — final.resolution mismatch + exit 1). Said "move on". Flag flipped to [COMPLETE].

#### Phase 1.3 — Test-mode mocks honor requested resolution  [COMPLETE]

**Description:** `shim.render_mock_image` and `shim.render_mock_video` currently emit fixed-size placeholders. Make them respect the requested resolution (image: portrait still at the project's aspect ratio via PIL; video: clip at the project's exact resolution via lavfi). Without this, resolution-adaptation cases either need real spend or pass false-positive against 1080×1920 placeholders.

**Acceptance:**
- Mock still output: aspect ratio matches project resolution within 0.5%; cropped from a square base, not stretched. ✅
- Mock video output: dimensions match project resolution exactly. ✅
- Mock voiceover: returns word timestamps that sum to the requested duration ±0.05s. ✅ (existing impl already satisfied this; guard test added)
- All existing 203 tests stay green. ✅ (210/210 after the new tests)

**Self-verification (subagent):** `uv run pytest -q`, plus three new tests asserting mock dimensions track plan resolution at 480x854 / 720x1280 / 1080x1920.

**Implementation notes:**
- `render_mock_image` now takes `resolution: str = "1080x1920"`. Renders a square base canvas (so prompt text stays at the same visual scale across resolutions), center-crops to the requested aspect, then resizes to exact target pixels with LANCZOS.
- `render_mock_video` already accepted `resolution`; openrouter dispatchers now thread `size` from `generate_image` / `generate_video` into the test_call lambdas (default `"1080x1920"` when unset, preserving back-compat).
- `_mock_voiceover` left unchanged — it was already emitting `anullsrc -t total` where `total` matches the synthesized word timestamps. New `tests/test_mocks_resolution.py::test_mock_voiceover_silence_matches_word_timestamps` guards this.
- `project.animate_scenes` still calls `render_mock_video` without a `resolution` kwarg; left as-is (its `resolution` param is `"480p"`-style not `WxH`, separate concern).

**Human verification steps:**
```sh
uv run pytest tests/test_mocks_resolution.py -v          # 7/7 pass
uv run pytest -q                                         # 210/210 pass
PARALLAX_TEST_MODE=1 uv run parallax verify-suite tests/fixtures/verify_suite_smoke/   # [PASS] basic
```

**Updates:**
- 2026-04-28 — Phase 1.3 complete on `refactor/openrouter-cli`. Commits: `24a462c` (shim) + `7e2f62a` (tests). +7 tests (210 total). All local; awaiting user signoff before flipping to merge-ready.
- 2026-04-28 — user signed off ("move on"); flag flipped to [COMPLETE].

#### Phase 1.4 — Reference test case + scaffolder  [COMPLETE]

**Description:** Ship one canonical test case at `tests/integration/res-720x1280/` — `plan.yaml`, `README.md`, `expected.yaml` populated with the full schema from Phase 1.2. Add `parallax verify-init <name> [--from <existing>]` to scaffold a new case folder by copying an existing one and rewriting its plan + expected. Lets new cases ship with a one-liner.

**Acceptance:**
- `parallax verify-suite tests/integration/res-720x1280/` passes in test mode (free, exit 0).
- `parallax verify-init res-480x854 --from res-720x1280` creates a new folder with `plan.yaml` resolution rewritten to 480x854 and `expected.yaml` updated to match.
- README in the reference case explains: how the schema works, how to author a new case by hand, how `--paid` interacts, where `parallax-demo/test_*` cases live and how they share the schema.

**Self-verification (subagent):** `uv run pytest -q`, scaffolder roundtrip (`verify-init res-480x854 --from res-720x1280`, `verify-suite res-480x854`, expect PASS), README readability check.

**Human verification steps:**
1. `uv run pytest -q` — expect `224 passed` (210 baseline + 14 new in `tests/test_verify_init.py`).
2. `PARALLAX_TEST_MODE=1 uv run parallax verify-suite tests/integration/res-720x1280/` — expect `[PASS] res-720x1280` and exit 0.
3. `uv run parallax verify-init --help` — expect a documented subcommand with `--from`, `--resolution`, `--force` flags.
4. Roundtrip:
   ```sh
   rm -rf /tmp/_p14_demo
   uv run parallax verify-init /tmp/_p14_demo --from tests/integration/res-720x1280/
   PARALLAX_TEST_MODE=1 uv run parallax verify-suite /tmp/_p14_demo/
   # expect [PASS] res-720x1280, exit 0
   ```
5. Resolution rewrite + roundtrip:
   ```sh
   rm -rf /tmp/_p14_480
   uv run parallax verify-init /tmp/_p14_480 --from tests/integration/res-720x1280/ --resolution 480x854
   PARALLAX_TEST_MODE=1 uv run parallax verify-suite /tmp/_p14_480/
   # expect [PASS] _p14_480, exit 0
   ```
6. Negative — re-run `verify-init /tmp/_p14_demo --from ...` without `--force`, expect `Error: target directory already exists` and exit 1. Re-run with `--force`, expect success.
7. Read `tests/integration/res-720x1280/README.md` — confirm it explains the full schema, the by-hand and `--from` authoring flows, `--paid` semantics, and the dual-folder convention.

**Updates:**
- 2026-04-28 — kicked off subagent.
- 2026-04-28 — subagent reported done. Commits on `refactor/openrouter-cli` (all local, none pushed):
  - reference case at `tests/integration/res-720x1280/` with full-schema `expected.yaml`, 720x1280 plan with captions+headline, operator-facing README.
  - `verify-init` scaffolder in `verify_suite.py` with `--from` / `--resolution` / `--force`. CLI subcommand wired. `--resolution` rewrites plan.yaml `resolution:`, `expected.final.resolution`, every per-stage `stages.<name>.resolution`, and the `name:` field — so a rewrite produces a working case, not one that needs hand-edits.
  - `run_suite` accepts a single case folder directly (single-case shortcut) so operators can iterate on one case without wrapping in a parent dir.
  - 14 new pytest tests in `tests/test_verify_init.py` (224 total, was 210). Includes a roundtrip test that scaffolds with `--resolution` and runs verify-suite to PASS.
  - AGENTS.md updated with `verify-init` doc + single-case shortcut for `verify-suite`.

**Phase 1.4 carry-overs:**
- `stages.stills.resolution` was originally omitted from the canonical case's `expected.yaml` because the `stage_stills` call into `openrouter.generate_image` did not pass `size` through, so test-mode mocks always rendered 1080×1920. Resolved 2026-04-29 — see top-of-log [CHANGED] entry; the assertion is now `720x1280` in the canonical case.

- 2026-04-28 — user signed off ("move on"); flag flipped to [COMPLETE]. Block 1 (Test Harness Foundation) closes — all 4 phases [COMPLETE]. Block 2 + Block 3 of the Integration Test Suite work block remain [INCOMPLETE] and are deferred per the 6-hour pivot to live agent-driven e2e (narrative-parallax Block 5).

### Block 2 — Test Case Matrix  [INCOMPLETE]

Populate the matrix using the Block 1 harness. Most of these are scaffolder + plan-edit work; the harness does the heavy lifting.

#### Phase 2.1 — Resolution adaptation (3 cases)  [INCOMPLETE]
`tests/integration/res-480x854/`, `res-720x1280/` (already exists from 1.3), `res-1080x1920/`. All free, test-mode. Each is a 2-scene plan with captions + headline. Verifies fontsize scaling, frame-size preservation through every burn pass, and that downstream stages don't hard-code dimensions.

#### Phase 2.2 — Image-model parity (3 cases)  [INCOMPLETE]
`img-gemini-3-flash/`, `img-nano-banana/`, `img-seedream/`. Marked `paid: true`. Each generates one still + one i2v clip + assembles + asserts that the final clip's first frame's aspect matches the project resolution within 0.5%. This is the case set that catches "model X stops honoring aspect_ratio" regressions.

#### Phase 2.3 — Pipeline-step coverage (5 cases)  [INCOMPLETE]
`pipeline-no-captions/`, `pipeline-no-headline/`, `pipeline-locked-stills/` (assets pre-committed in folder), `pipeline-locked-clips/`, `pipeline-avatar/` (lavfi-generated blue source). All free, test-mode. Verifies each conditional branch in `produce.py` runs cleanly in isolation.

#### Phase 2.4 — Voice routing (2 cases)  [INCOMPLETE]
`voice-gemini/` (default Gemini path), `voice-elevenlabs/` (`voice: eleven:<id>`). Free in test mode (mocks WhisperX + voice synth); `--paid` triggers real synth.

### Block 3 — Smoke + Documentation  [INCOMPLETE]

#### Phase 3.1 — Paid full-fidelity smoke  [INCOMPLETE]
One 4-scene case at house defaults run against real APIs end-to-end. The thing to run before any release. Exists in the matrix as `smoke-full/`, marked `paid: true`. Phase delivers the case + a `parallax verify-suite tests/integration/smoke-full/ --paid` invocation that's documented as the canonical pre-release sanity check.

#### Phase 3.2 — AGENTS.md + dual-folder docs  [INCOMPLETE]
Document the verify-suite workflow in AGENTS.md (commands, flags, schema, how to author a new case). Cover the dual-folder convention: `tests/integration/` for repo-versioned free cases, `~/Documents/parallax-demo/test_<n>/` for ad-hoc paid playgrounds the operator points at manually. The latter borrow the same `expected.yaml` schema so the runner works on both.

### WHY-prompts (open questions before this block locks)

1. **Long-term role of parallax CLI.** Is this the final big investment in the CLI before attention pivots to the `narrative-parallax` agent layer, or will the CLI keep evolving in parallel? Recommendation: assume the former and design the test contract to be *strict* (exact resolutions, exact scene counts, contiguous-coverage invariants asserted hard). If the CLI is going to keep moving, we'd loosen some assertions and lean harder on Phase 3.2's docs so future-you can author new cases as the surface grows.

2. **In-repo `tests/integration/` vs `~/Documents/parallax-demo/test_*`.** I scoped both above, but they serve different purposes — in-repo cases are version-controlled regression scenarios you run on every change; `parallax-demo` cases are operator playgrounds you point at when you want to manually validate something specific. Recommendation: do both, with the same `expected.yaml` schema. In-repo for free CI-style cases, `parallax-demo` for paid real-asset scenarios that don't belong in git history.

3. **Patchwork or rewire — `produce.py` posture.** Building real integration tests will expose every weak spot in `produce.py`'s 700-line procedure (missing flags, hard-coded assumptions, branches we can't currently invoke in isolation). Recommendation: hold the line in this block — document gaps as `[FUTURE]` entries in DEV_LOG, ship the test harness against the surface that exists today, and scope a separate `produce.py refactor` block once the matrix is in place. Otherwise this block balloons. If you'd rather treat integration testing as the forcing function for the refactor, we'd merge this with a `produce.py` decomposition phase and double the size of the block.

The 1,979-line `tools_video.py` was extracted into eight focused modules in eight separate commits, each running the full 165-test suite green before moving on:

1. `parallax.captions/` subpackage — `styles`, `chunker`, `animation`, `drawtext`, `pillow`, `burn`.
2. `parallax.manifest` — `write_manifest`, `read_manifest`.
3. `parallax.ffmpeg_utils` — `_get_ffmpeg`, `_ffmpeg_has_drawtext`, `_probe_fps`, `_parse_color`, `_FFMPEG_FULL`.
4. `parallax.assembly` — `align_scenes`, `ken_burns_assemble`, `assemble_clip_video`, `_zoom_filter`, `_make_kb_clip`, `_make_clip_segment`.
5. `parallax.avatar` — `generate_avatar_clips`, `key_avatar_track`, `burn_avatar`.
6. `parallax.headline` — `burn_titles`, `burn_headline`.
7. `parallax.voiceover` — `generate_voiceover`, `_apply_atempo`, `_trim_long_pauses`, `_mock_voiceover`.
8. `parallax.project` — `scan_project_folder`, `animate_scenes`.

`tools_video.py` is now a 72-line compat shim that re-exports the public surface — kept (not deleted) because external imports may still reference `parallax.tools_video.<name>`. New code should import from the extracted module directly. AGENTS.md gained a "Module map" section enumerating which functions live where.

Pure mechanical move — no signatures changed, no helpers combined, no renames. Phase-1 characterization tests (added in `0e51f29`) were the safety net; running `uv run pytest -q` between every move surfaced any breakage immediately.

`headline.py` imports `CAPTION_STYLES` and `_FONTS_DIR` from `captions.styles` — that's an inherent pre-existing dependency (titles share the font/style presets), not a new one introduced by the split. Flagged for review post-split: the import surface between `headline` and `captions.styles` could be tightened to a thin "fonts" interface if we ever want to make captions truly leaf.

**Breaks if:**
- Final mp4 is shorter than the wav (assembly tail-cover regression — `align_scenes` + `ken_burns_assemble` mux step)
- Captions display at wrong size or wrong position (`_style_drawtext_filter` filter graph regression)
- Avatar appears as a blue rectangle instead of being keyed (chroma-key chain — `key_avatar_track` ProRes 4444 alpha or `burn_avatar` overlay format=auto)
- `parallax produce` errors on import (a module move broke a consumer import — most likely `produce.py`'s inline `from .tools_video import _zoom_filter, _get_ffmpeg, _make_kb_clip` paths, which now resolve via `assembly` and `ffmpeg_utils` directly)

## 2026-04-28 — [CHANGED] TTS style presets + plan YAML support; rapid_fire is the ad default

Gemini Flash TTS has no numeric speed/rate parameter — speech control is **prompt-based only**. Live A/B (verified): the bare prompt produces 13.13 s for a chocolate-bar ad script; prefixing `"Read this as a rapid-fire commercial — talk fast, no pauses, urgent, energetic. Speak quickly: "` brings the same script to **8.45 s, a 36 % speedup with no audible distortion**.

Codified as `STYLE_PRESETS` in `parallax/gemini_tts.py`:
- `rapid_fire` — the verified-aggressive directive. **DEFAULT**, applied automatically when no `style` / `style_hint` is passed to `generate_tts(alias='gemini-flash-tts')`.
- `fast` — milder energy bump.
- `calm` — measured / conversational.
- `natural` — empty directive (opt out of the ad-default for non-ad copy).

Threading: `gemini_tts.synthesize(style=, style_hint=)` → `openrouter.generate_tts(style=, style_hint=)` → `tools_video.generate_voiceover(style=, style_hint=)` → plan YAML fields `style:` / `style_hint:`. `style_hint` (freeform string) wins over `style` (preset name) when both are given. Unknown style names raise loudly so plan YAML typos don't silently fall back to `natural`.

Parallax CLI defaults flipped to align with the user's primary path:
- `voice` default `george` (ElevenLabs) → `Kore` (Gemini)
- `speed` default `1.1` (atempo for ElevenLabs) → `1.0` for Gemini, `1.1` retained when `voice` starts with `eleven:`
- `tools_video.generate_voiceover` now routes by voice prefix: `eleven:<id>` → ElevenLabs path; anything else → Gemini path

Five new tests in `test_gemini_tts.py`: rapid_fire is the default through the production path, `style='natural'` skips the directive, `style_hint` overrides preset, unknown style raises, response missing audio parts raises. Total: 68 passed.

Live re-verification at 8.45 s (default-no-style path through `openrouter.generate_tts`) confirms the production default matches the verified A/B winner.

**Breaks if:** `generate_tts(alias='gemini-flash-tts')` without explicit `style` produces audio noticeably longer than ~9 s for the chocolate-ad script (the rapid_fire directive isn't being prepended); plan YAML `style: rapid_fire` produces a different cadence than no-arg (the YAML field isn't being read or threaded); or `style: <typo>` silently falls back to natural (must raise `ValueError: Unknown TTS style`).

## 2026-04-28 — [CHANGED] Gemini Flash TTS wired as primary voiceover (direct Google API)

OpenRouter does **not** host Gemini TTS (verified: `/api/v1/models` audio-output filter shows only Lyria music + OpenAI gpt-audio, none with per-word alignment). Wired Gemini Flash Preview TTS via the `google-genai` SDK directly: new `parallax/gemini_tts.py` exposes `synthesize(text, voice, out_dir, api_key)` returning `(wav_path, words, duration)`. Audio is 24 kHz mono 16-bit PCM wrapped as WAV.

`generate_tts` now routes by alias: `voice='eleven:<id>'` → ElevenLabs (escape hatch with native alignment); alias starts with `gemini` → direct Gemini API; anything else falls into the OpenRouter `_tts_real` path (still raises until OpenRouter ships an alignment-emitting TTS model). Default voice is `Kore`; ~30 prebuilt voices documented in the module.

`pricing.py` simplified — removed `gpt-4o-mini-tts` and `voxtral` aliases. They pointed at OpenRouter slugs that either don't exist or don't emit alignment; ElevenLabs (per-voice fidelity) and Gemini (free preview, default) cover the actual use cases. The Gemini ModelSpec uses a sentinel `model_id="gemini-direct/gemini-2.5-flash-preview-tts"` to mark it as out-of-OpenRouter.

**Gotcha — Gemini does not return word timestamps.** Per-word `start`/`end` are evenly distributed across total duration. For tighter caption sync, layer forced alignment (whisper) on the produced wav, or use `voice='eleven:<id>'`.

Five new tests in `test_gemini_tts.py`: even-distribution math, empty-input handling, missing-key raises, mocked-client wav-format roundtrip, missing-audio-parts raises. Updated `test_pricing.py` to expect only `gemini-flash-tts` in TTS_MODELS. Total: 64 passed.

Live demo verified: 10s "Tokyo neon" narration via `voice='Kore'` produced a clean 481 KB wav at `~/Documents/narrative-demo/`.

**Breaks if:** `generate_tts(alias='gemini-flash-tts')` calls OpenRouter instead of Google's Gemini API; the produced wav is not 24 kHz mono 16-bit; missing `AI_VIDEO_GEMINI_KEY`/`GEMINI_API_KEY` produces a stub or `ModuleNotFoundError` instead of a clear `RuntimeError` mentioning the env var; or Gemini TTS pricing leaves preview and the `cost_usd=0.0` ModelSpec leaks free-tier costs into usage records.

## 2026-04-28 — [FIX] Real-mode media gotchas codified in tests; size/aspect_ratio knobs wired

Live API verification surfaced four wire-shape bugs and gaps in the previous round. Each is now locked into `test_openrouter_request_shapes.py` (10 tests using fake httpx) so a future regression fails loudly instead of producing a runtime ZodError 400 in front of the user:

1. **`frame_images` must be an array, not an object.** First impl shipped `{"first_frame": {...}}`; correct shape is `[{"type": "image_url", "frame_type": "first_frame", "image_url": {"url": ...}}]`. `frame_type` ∈ `{"first_frame", "last_frame"}` per model spec — last-frame conditioning is supported by every video model that lists it in `supported_frame_images`. (veo-3.1-fast supports both; seedance and kling support first_frame.)
2. **`model_id` namespace stripping.** pricing.py prefixes everything with `openrouter/` so the dispatcher can tell at a glance which backend a row belongs to. The wire `model` parameter strips the leading `openrouter/`. Locked in via `test_*_strips_openrouter_prefix_from_model_id`.
3. **`size` and `aspect_ratio` passthrough.** `generate_image(size=...)` and `generate_video(size=..., aspect_ratio=...)` now pass through to the API. Each video model's `supported_sizes` is enumerable via `/api/v1/videos/models`; submit-time validation surfaces "size not in supported list" errors with the request id intact.
4. **`gemini-2.5-flash-image` ignores size hints.** Verified live: `size='1080x720'`, `aspect_ratio='3:2'`, `width/height` — all silently produce 1024×1024. Documented as a model-level limitation in the `generate_image` docstring; for exact-dimension output use a different image model or post-process resize via `tools_video`.

`pricing.py` `model_id`s for video aliases corrected against the live `/api/v1/videos/models` catalog: `kling` → `kwaivgi/kling-video-o1`, `seedance` → `bytedance/seedance-2.0-fast`, `wan` → `alibaba/wan-2.7`. `veo` and `sora` were already correct.

Capability matrix per video model (from `/api/v1/videos/models`, retain inline so a fresh agent doesn't have to re-query):

| alias | model_id | aspects | sizes (subset) | durations | first_frame | last_frame |
|-------|----------|---------|----------------|-----------|-------------|------------|
| veo | google/veo-3.1 | 16:9, 9:16 | 1280×720, 1080×1920, 1920×1080, 720×1280, 4K | 4, 6, 8 | ✓ | ✓ |
| seedance | bytedance/seedance-2.0-fast | 1:1, 3:4, 9:16, 4:3, 16:9, 21:9, 9:21 | 480p–720p variants | 4–15 | ✓ | ✗ |
| kling | kwaivgi/kling-video-o1 | 16:9, 9:16, 1:1 | 1280×720, 720×1280, 720×720 | 5, 10 | ✓ | ✗ |
| wan | alibaba/wan-2.7 | per spec | per spec | per spec | per spec | per spec |
| sora | openai/sora-2-pro | per spec | per spec | per spec | per spec | per spec |

Querying the live catalog with `httpx.get("https://openrouter.ai/api/v1/videos/models")` is the source of truth — quarterly cross-check recommended.

Live end-to-end demo: vertical Tokyo-street still (`google/gemini-2.5-flash-image`) animated to 4-second video (`bytedance/seedance-2.0-fast`, $0.48), narrated 10s via `voice='Kore'` Gemini Flash TTS. All four artifacts in `~/Documents/narrative-demo/`.

**Breaks if:** `frame_images` is sent as an object; the wire `model` parameter still has the `openrouter/` prefix; `size`/`aspect_ratio` are sent on requests where they weren't asked for (must be omitted, not null); or any of the corrected video model_ids drifts again. The capability matrix above is a snapshot — re-query `/api/v1/videos/models` if a model's behavior changes unexpectedly.

## 2026-04-28 — [CHANGED] Real OpenRouter HTTP wired for image AND video; pricing.py model_ids corrected

Verified live against the user's `OPENROUTER_API_KEY`:

- **Image:** `_image_real` POSTs `/api/v1/chat/completions` with `modalities=["image","text"]`. Response carries `message.images[0].image_url.url` as a base64 data URL. Verified end-to-end against `google/gemini-2.5-flash-image` — returns 1024×1024 RGB PNG. Reference images supported as `image_url` content parts.

- **Video:** discovered via `/api/v1/videos/models` (11 models). `_video_real` now wires the verified async contract: POST `/api/v1/videos` `{model, prompt, duration, frame_images?}` → 202 `{id, polling_url}` → poll until `status=="completed"` → GET `unsigned_urls[0]` for the mp4 bytes. Verified live with `bytedance/seedance-2.0-fast` (4s clip, ~2 min wall, $0.48 per call). Default poll interval 5 s, timeout 10 min.

- **TTS:** `_tts_real` still raises with a clear "use `voice='eleven:<id>'`" message — OpenRouter's audio-output models (gpt-audio, lyria) are conversational/musical and lack per-word alignment.

`pricing.py` model_ids corrected to match the actually-hosted slugs from `/api/v1/videos/models`:

- `kling` was `kuaishou/kling-video-o1` → now `kwaivgi/kling-video-o1`
- `seedance` was `bytedance/seedance` → now `bytedance/seedance-2.0-fast`
- `wan` was `alibaba/wan-2.5` → now `alibaba/wan-2.7`
- `veo`, `sora` already correct (`google/veo-3.1`, `openai/sora-2-pro`)

Earlier `[GOTCHA]` entry retracted: I queried only `/api/v1/models` (which lists synchronous chat-style models) and saw zero video output_modalities — false negative. OpenRouter's video models live on a separate listing endpoint with submit-poll-download semantics, not the chat endpoint.

Three new tests in `test_openrouter.py`: live `test_image_real_mode_round_trip` (skipped without `OPENROUTER_API_KEY`); fake-key fallback for video and TTS to confirm error wrapping. Total: 51 passed.

Rejected: a synchronous `_video_real` returning a Future. The polling loop is internal because callers expect a Path return — exposing async would force every handler in the dispatcher (`_with_fallback`, `_record_usage`) to become async. Internal poll keeps the contract synchronous.

**Breaks if:** `_video_real` exits the polling loop without raising on `status=="failed"` or `"error"`; the produced mp4 is 0 bytes (likely the `unsigned_urls[0]` requires the auth header — currently we send it); video poll exceeds 10 min and the timeout doesn't surface as a `RuntimeError` mentioning the last status; or any of the four corrected `model_id`s drifts again (cross-check `/api/v1/videos/models` quarterly).

## 2026-04-27 — [CHANGED] ElevenLabs synthesis migrated into `parallax/elevenlabs.py`

Carved a dedicated `parallax/elevenlabs.py` module out of `tools_video.generate_voiceover`. It owns three things only: the `VOICE_IDS` shorthand table, `resolve_voice(voice, api_key)`, and `synthesize(text, voice_id, out_dir, api_key) -> (raw_path, words, duration)`. The HTTP call to `convert_with_timestamps` and the character-alignment-to-word-timestamps derivation now live here, with no parallax-pipeline assumptions baked in.

`openrouter._tts_elevenlabs` is now the canonical narrative-facing entry point: it handles the test-mode shim, validates `AI_VIDEO_ELEVENLABS_KEY`/`ELEVENLABS_API_KEY`, calls `elevenlabs.synthesize`, and records usage via `_usage.record(backend="elevenlabs", ...)`. The previous `NotImplementedError` placeholder is gone. `tools_video.generate_voiceover` is now a thin orchestration layer: it resolves the voice, calls `openrouter.generate_tts(voice="eleven:<id>")`, applies `_apply_atempo` + `_trim_long_pauses`, writes `vo_words.json`, and returns the JSON-string contract that `produce.py` already expects.

Six new tests in `tests/test_elevenlabs.py` cover: alias short-circuit (no API call for known names), raw-id pass-through, `cost_for` math, character-alignment word grouping (acoustic-end vs. fallback), test-mode runs without a key, and real-mode missing-key surfaces a clear `RuntimeError`. Total: 48/48 passing.

Rejected: leaving the synthesis path in `tools_video` and having `_tts_elevenlabs` call back into it. That would have been a circular ownership loop — the agent-facing entry point (`openrouter.generate_tts`) calling into a video-pipeline tool to get raw audio. Now ownership flows the right way: `elevenlabs` (synthesis primitive) ← `openrouter` (canonical wrapper, usage recording) ← `tools_video` (parallax-pipeline pacing on top).

**Breaks if:** `voice="eleven:<id>"` in real mode raises `NotImplementedError` instead of calling ElevenLabs; `tools_video.generate_voiceover` still imports `from elevenlabs.client`; `~/.parallax/usage.ndjson` records the ElevenLabs spend with `backend="tools_video"` instead of `backend="elevenlabs"`; or `_apply_atempo` / `_trim_long_pauses` no longer run after a real-mode synthesis (the `vo_words.json` `total_duration_s` should be ~`speed×` shorter than the raw mp3 duration).

## 2026-04-27 — [FIX] cost_usd aggregation now keyed by run_id, not session_id

`run.end` was reporting cost from previous runs because `session_id` collides across `parallax produce` invocations of the same plan (`produce-<plan_stem>`), so `_usage.session_total()` summed every run that had ever touched that plan. Added a `run_id` field to `UsageRecord` (auto-populated from `runlog.current_run_id()` when not passed), introduced `usage.run_total(run_id)`, and switched `produce.py` to compute `run_cost` via `run_total(run_id)`. `cost.json` now records both `run_id` and `session_id`; `runlog.end_run` and `cost.json.cost_usd` no longer leak prior-run spend.

session_id is kept on the record for back-compat with existing `parallax usage` aggregates and the by-session reporting it does — only the per-run summary at end-of-produce switched.

**Breaks if:** running `parallax produce` twice in a row in real mode shows the second run's `run.end cost_usd` equal to the sum of both runs (it should equal only the second run's spend); `~/.parallax/usage.ndjson` records written after this change are missing the `run_id` field.

## 2026-04-27 — [DECISION] FAL → OpenRouter unified seam; ElevenLabs as escape hatch

Replaced the FAL-direct image/video pipe with `openrouter.py` — one client with three entry points (`generate_image`, `generate_video`, `generate_tts`), one alias table per kind in `pricing.py` (image: draft/mid/premium/nano-banana/seedream; video: kling/veo/seedance/wan/sora; tts: gemini-flash-tts/gpt-4o-mini-tts/voxtral). Each spec carries a `fallback_alias`; `_with_fallback` walks the chain on RuntimeError. `tools.py` is now a 1-call delegate; `fal.py` deleted. ElevenLabs is retained only as `voice: eleven:<voice_id>`.

Real-mode HTTP for OpenRouter is intentionally NOT implemented yet — `_image_real`/`_video_real`/`_tts_real` raise `NotImplementedError` with a clear hint. Rationale: fetching the OpenRouter media-API contract returned 404, and shipping speculative request bodies risks silent breakage when a real key is wired. The seam is the value; the HTTP body gets verified the moment OPENROUTER_API_KEY is set. Test mode (`PARALLAX_TEST_MODE=1`) routes everything through `shim.py` (PIL stills, ffmpeg-looped stub mp4s, sine-wave WAVs with wpm-derived word timings) and exercises the full pipeline end-to-end.

Also landed in this sprint: `runlog.py` per-run JSONL event log at `~/.parallax/logs/<run_id>.log`; `parallax tail <run_id>` (and `tail latest -f`) subcommand; per-scene timing-override fields (`duration_s`, `start_offset_s`, `fade_in_s`, `fade_out_s`) with cascade. Stale tests for deleted backends/sessions/dispatcher/update_check removed; new `test_openrouter.py` covers every alias × every kind in test mode plus fallback-exhaustion in real mode.

**Breaks if:** `PARALLAX_TEST_MODE=1 parallax produce --folder fixtures/test_concept --plan fixtures/test_concept/plan.yaml` does not produce a final mp4 in <5s; `parallax tail latest` does not show `run.start` → `plan.loaded` → `run.end` events; `uv run pytest` reports anything other than 42/42; or an `OPENROUTER_API_KEY` set in real-mode produces a render (it must still raise `NotImplementedError` until the HTTP impl lands).

## 2026-04-24 — [CHANGED] trim_silence: avatar track is audio source of truth

`trim_silence` now trims the avatar track (video+audio together) first, then extracts audio FROM the trimmed avatar. Previously it trimmed audio and avatar independently, which left open the possibility of drift. Since the avatar track already carries the correct audio, extracting it directly guarantees sync. Output audio is now named after the avatar (e.g. `avatar_track_trimmed_v3.mp3`) rather than the old standalone `avatar_audio_trimmed_v2.mp3`.

**Why:** the avatar track is a sync-locked video+audio pair. Trimming them as separate streams with independent filters can produce subtle clock drift. Extracting audio from the already-trimmed video eliminates that path entirely.

## 2026-04-24 — [GOTCHA] _trim_video with H.264 breaks downstream chromakey

Encoding avatar track to H.264 (`libx264 -pix_fmt yuv420p`) during silence removal caused the chroma key in `key_avatar_track` to key out the entire frame (100% transparent output). The original avatar had `unknown` color range; H.264 re-encode tags the output `color_range=tv` (limited). This changes how `format=yuva444p12le,chromakey` interprets Cb/Cr values relative to what the key color `0x78C2C9` expects.

**Fix:** `_trim_video` now outputs ProRes 422 HQ (`.mov`) with `prores_ks -profile:v 3`. ProRes preserves pixel data at high fidelity and has consistent color range metadata that `key_avatar_track` handles correctly.

**What NOT to do:** never re-encode an avatar track to H.264 mid-pipeline. If you need a container change, use ProRes or FFV1. Any codec that alters color range metadata will silently break the chroma key.

## 2026-04-24 — [FIX] produce.py required avatar.image even when avatar_track was pre-provided

`avatar.image` (or `character_image`) was checked unconditionally at the top of the avatar block in `run_plan()`, but `avatar_img` is only used in the `generate_avatar_clips` branch (when no `avatar_track` is given). Any plan with a pre-recorded `avatar_track` but no `character_image` would error out with "avatar.image or character_image required for avatar overlay." Moved the image check inside the `else` branch (generate path only).

**Root cause:** image check was added before the `if avatar_track_raw / else` branch, so it fired regardless of which path was taken.

## 2026-04-24 — [CHANGED] parallax audio trim-silence — new first-class command

Added `parallax audio detect-silences` and `parallax audio trim` to replace the ad-hoc FFmpeg split/concat approach agents were using. Uses `aselect`/`select` filters for frame-accurate removal without keyframe alignment issues. Updates `plan.yaml` in-place with versioned new file references. See `audio.py: detect_silences`, `trim_silence`, `_trim_audio`, `_trim_video`, `_adjust_words`.

**Why the old approach failed:** Splitting a video at a non-keyframe boundary with `-c:v copy` cuts at the nearest GOP, producing wrong durations. Concatenating re-encoded parts creates PTS discontinuities at the join that manifest as a freeze frame. `select` filter decodes every frame and removes ranges precisely with no concat.

## 2026-04-23 — [FUTURE] tools_video.py module breakup — deferred, thin API first

`tools_video.py` grew to 70KB during rapid video pipeline development. Target module structure is documented in `VISION.md` (Module Architecture section): `parallax/audio.py`, `parallax/video.py`, `parallax/stills.py` as domain-namespaced public surfaces over the monolith.

**Why deferred:** The monolith is working production code. A big-bang refactor risks regressions across every caller in `produce.py`. Phase 1 (thin public API) gives agents and callers the correct namespace immediately with zero risk. Full extraction happens function-by-function as code is touched for real reasons.

**Priority target:** The narration pipeline (`generate_voiceover`, `_apply_atempo`, `_trim_long_pauses`, fixed-WPM fallback) is the largest self-contained chunk and has no video dependencies — extract it as a unit into `parallax/audio.py` when next touching audio behavior.

**How to apply:** When adding any new utility (transcribe, extract_frame, sample_color, etc.), put it in the domain module directly — not in `tools_video.py`. Only move existing functions when you're already editing them for another reason.

## 2026-04-21 — [CHANGED] Removed agent backends; CLI is now produce-only

Deleted `claude-code` and `anthropic-api` backends along with session tracking, the `parallax run` command, and the update-check nag. The CLI now has exactly one pipeline entry point: `parallax produce --folder <path> --plan <plan.yaml>`. `current_backend` ContextVar removed — usage records hardcode `backend="produce"`. AGENTS.md and env var table updated to remove all agent/backend references.

**Breaks if:** `parallax produce` fails to write usage records (the `backend` field in `~/.parallax/usage.ndjson` should read `"produce"`).

## 2026-04-20 — [FIX] drawtext escape order: backslash before colon

`_style_drawtext_filter` and `burn_headline` were running `replace(":", "\\:")` before `replace("\\", "\\\\")`. This doubled the backslashes inserted by the colon escape — turning `\:` (correct escaped colon) into `\\:` (literal backslash + option delimiter). Any word containing `:` (e.g. `Week 1:`) would corrupt the filter chain and break all subsequent drawtext filters.

Fix: reordered to backslash-first — `replace("\\", "\\\\")` then `replace(":", "\\:")`. Backslashes in the original text are doubled (safe), then colons are escaped, and the newly introduced `\` is not touched again.

**What NOT to do:** never run `replace("\\", "\\\\")` after `replace(":", "\\:")` — the first inserts backslashes that the second will then double.

## 2026-04-20 — [CHANGED] Clip Assembly Pipeline: video clip mode for numbered asset folders

Added a second pipeline mode alongside Ken Burns. `scan_project_folder` now detects folders that contain numbered clips (`001.mp4`, `002.mov`, `011.png`, etc.) and returns `mode='video_clips'` plus a `clips` dict mapping clip number → file path. When mode is `video_clips`, the agent uses `assemble_clip_video` instead of `generate_image` + `ken_burns_assemble`.

`assemble_clip_video` takes aligned scenes (each with `clip_paths` and `duration_s`), normalizes every clip to a uniform resolution/fps, loops or trims each scene to its voiceover duration, concats all scenes, and mixes in the voiceover audio. PNGs are converted to a 2s video before looping.

**Why:** the Fitbod Simpsons animation project has 19 numbered clips that map 1:1 to script scene markers `[001]`, `[002-004]`, etc. The Ken Burns pipeline isn't applicable here — the clips already exist. The model parses the `[NNN]` markers to build `clip_paths` per scene, aligns with voiceover word timestamps, then assembles.

**Breaks if:** `assemble_clip_video` produces a video with wrong total duration (alignment or loop-trim logic off), clip scenes appear in wrong order (scene index ordering), a PNG clip produces a black segment (ffmpeg `-loop 1` command), or `scan_project_folder` returns `mode='video_clips'` on a Ken Burns project that happens to have 3+ numbered files.

## 2026-04-20 — [GOTCHA] Bangers font: glyph clipping in drawtext — deferred

Bangers (display font with extreme italic slant) renders glyphs that extend above the font's declared ascender bounding box. ffmpeg's `drawtext` clips anything outside the declared text bounding box (`th`), so the tops of tall letters get cut regardless of position. Attempted fix was scaling the x centering (`tw*1.45`) to shift text left and give the slant room — that fixed neither the top clipping nor kept the text centered (it just shifted the whole word left).

Root cause: the clipping is not a frame-boundary issue — it's drawtext's internal render clipping against the font's declared glyph metrics. The fix is likely one of: (a) add a `y` offset to push text down so the declared box sits lower and clipped tops are off-screen instead of in-frame, (b) use `expansion=none` + manual line height overrides, or (c) switch to a font with correct metrics (Bebas Neue, Anton, Impact all render clean).

**Do NOT attempt to fix with x centering multipliers** — they shift the text off-center without helping the clip. The y/ascender metrics are the real lever.

**What NOT to do next time:** don't try `(w-tw*N)/2` style x hacks. The glyph clips at the top, not the side.

## 2026-04-20 — [CHANGED] boxer_v2: reassembly from existing assets, full pipeline

Ran the full post-production pipeline (write_manifest → align_scenes → ken_burns_assemble → burn_captions → burn_headline → write_manifest final) against pre-existing stills and voiceover — no image or audio generation. Final output: `output/boxer_v2_final.mp4` (1080×1920, 11.57s), Bangers captions 1-word-at-a-time, "SHE TRAINS ALONE" headline at y=10%. `align_scenes` fell back to the explicit timings from the brief (JSON parse issue when given a file path — the tool's path-read branch failed in this agent context). Manifests at `output/boxer_manifest.yaml` and `output/boxer_manifest_final.yaml`.

**Breaks if:** `output/boxer_v2_final.mp4` doesn't open, captions are missing or unstyled, or the headline doesn't appear at the top of the frame.

## 2026-04-20 — [GOTCHA] ffmpeg drawtext filter absent in Homebrew's minimal build
Homebrew ffmpeg 8.1 on this machine was built without `--enable-libfreetype`, so `drawtext` filter isn't in the binary. `burn_captions` was failing with "No such filter: 'drawtext'" on every run. Fixed by adding `_ffmpeg_has_drawtext()` probe and a Pillow-based fallback (`_burn_captions_pillow`) that decodes frames via rawvideo pipe, draws text with PIL/ImageDraw, re-encodes, then muxes audio back. The drawtext path is preferred when available (faster). Full pipeline now completes end-to-end in TEST_MODE.

**What NOT to do:** assume `drawtext` is universally available — it requires a ffmpeg built with `--enable-libfreetype`. The Homebrew formula used to include it, but the current build on this machine does not. Always probe first.

## 2026-04-20 — [CHANGED] video-pipeline-20260420: full pipeline e2e test passes
scan_project_folder → generate_image ×6 → generate_voiceover → align_scenes → ken_burns_assemble → burn_captions all succeed in PARALLAX_TEST_MODE=1. Final captioned video at `output/aria_ad_final.mp4` (2MB, 20.8s, 1080×1920). Agent followed pipeline order correctly. No real API calls made in test mode.

## 2026-04-18 — [FUTURE] Next swings: video support + multi-user/multi-project web UI
Two big tracks ahead of v0, explicitly not v0-scoped:

1. **Video support** — the manifest-first video pipeline per VISION.md. HoP agent (brief → scene list), Editor agent (scene list → `.parallax/manifest.yaml` with per-shot asset specs), deterministic `parallax compose` (walks manifest, calls `generate_image` + ffmpeg + video-production skill's trim-silence / captions / headline steps). Lives in a separate repo that depends on `parallax-v0` as a dep — v0 stays a standalone still-gen primitive. Gated on having a real concrete video brief to force the manifest schema; designing the schema in the abstract is the documented anti-pattern (per earlier FUTURE entry).

2. **Simple web UI with multi-user + multi-project support** — Plexi-shaped prototype that wraps the parallax CLI so creative users never see Terminal. Creative directors aren't going to `cd` into project folders; the CLI is the primitive, not the interface. Weekend-sized scope for the v1 of the UI:
   - FastAPI backend shells out to `parallax run`, parses stdout for session/cost/paths.
   - Per-user auth + per-user session isolation (each user's runs scoped to their own sessions dir + usage log).
   - Per-project scoping (text box for brief, model picker, per-project output folder, gallery view scoped to the project).
   - Block on subprocess, show spinner. No streaming in v1.
   - Deployment: MBA-on-LAN initially, cloud-hosted once a remote client needs it. Google Drive sync (via Drive for Desktop, stream mode) is the distribution layer — images land in a shared Drive folder, clients consume from Drive on any device.

**Not in scope for either track:** full Plexi App Protocol (trust floats, companion app, state buckets — per memory that's a multi-week build, not a weekend); streaming tool-call UI; built-in Drive sync as a parallax feature (keep parallax writing to `$PARALLAX_OUTPUT_DIR`, let a separate sync layer do cloud — matches "v0 is the still-gen primitive" framing).

**Sequencing decision still open:** video v1 vs. web UI, which goes first. Depends on whether a real video brief is in hand. If yes, video. If no, web UI is the higher-leverage swing because without a usable surface the video pipeline has no consumer.

## 2026-04-18 — [CHANGED] v0.1.4 patch: cached update-check nag on startup (tag v0.1.4)
Every `parallax` invocation now does a best-effort version check. Fire-and-forget at the start of `cli.main()`; never raises, swallows all errors (network, parse, filesystem). Hits `api.github.com/repos/ianjamesburke/parallax-v0/releases/latest` at most once per 24h, caches at `~/.parallax/.update_check`. Prints a single-line nag to stderr when the installed version is behind: `[parallax] A new version is available: vX.Y.Z (you have vA.B.C). Run: parallax update`. Opt out with `PARALLAX_NO_UPDATE_CHECK=1`. Skipped during `parallax update` itself.

Rejected: `packaging.Version` for semver compare (adds a dep for a 5-line helper). `git ls-remote --tags` (requires shelling out, more surface area than a 3s HTTPS GET). Auto-upgrading in-place (silently mutating the user's install is invasive and collides with explicit `parallax update` as the contract).

Version comparison uses tuple-of-ints on dot-split segments, trailing non-numeric chars dropped per segment. Good enough for x.y.z; prerelease tags compare by numeric prefix, which is close enough for a nag (we'll revisit if/when we ship prereleases).

**Breaks if:** a fresh install prints the nag (no cached value + same version as latest → fetcher returns current version → comparison is not-newer → silent), the nag triggers more than once per 24h on the same machine (cache TTL check is broken), an offline machine sees `parallax run` fail or hang on startup (fetcher has a 3s timeout and errors are swallowed), `parallax update` itself prints the nag (we explicitly skip during that subcommand), or `PARALLAX_NO_UPDATE_CHECK=1` still hits the network (the env check short-circuits before fetcher is called).

## 2026-04-18 — [CHANGED] v0.1.3 patch: backend auto-fallback + installer prompts ANTHROPIC_API_KEY (tag v0.1.3)
Two related fixes so "install on any Mac" actually works without Claude Code.

Backend dispatcher now auto-falls-back. Previously, default = `claude-code` and the call hard-failed if the `claude` CLI wasn't on PATH — even when `ANTHROPIC_API_KEY` was set and the anthropic-api backend would have worked. New behavior: when the user hasn't passed `--backend` or set `PARALLAX_BACKEND`, probe for `claude` CLI; if missing, fall back to `anthropic-api` iff `ANTHROPIC_API_KEY` is set; otherwise raise a message listing both setup paths. Explicit picks (CLI flag or env var) still hard-fail on missing prereq — we never silently override what the caller asked for.

Installer (`scripts/install.sh`) now prompts for `ANTHROPIC_API_KEY` when the `claude` CLI is absent. When `claude` is present, skip the prompt entirely — they're already set. Both keys (plus FAL_KEY) get written into a single `# >>> parallax env >>>` marker block so re-running the installer sees it and skips re-prompting (idempotent at the block level, not the per-key level — editing keys is a manual zshrc edit).

Rejected: prompting for `ANTHROPIC_API_KEY` unconditionally. Most users who install Claude Code don't want a second Anthropic key lying around — it'd bill separately and creates two auth paths to reason about.

**Breaks if:** `parallax run` with `claude` CLI absent and `ANTHROPIC_API_KEY` set still raises "claude\` CLI required" instead of routing to anthropic-api; `parallax run --backend claude-code` silently downgrades to anthropic-api when the CLI is missing (it must hard-fail — explicit picks are not overridable); re-running `scripts/install.sh` on a machine that already has the parallax env block appends a second one (the marker-block check should skip entirely); smoke test runs when no backend is configured.

## 2026-04-18 — [CHANGED] v0.1.2 patch: curl-pipe-sh installer (tag v0.1.2)
`scripts/install.sh` is the one-liner install story: bootstraps `uv` if missing, runs `uv tool install`, prompts once for `FAL_KEY` via `/dev/tty` (works through curl | sh), persists it to `~/.zshrc` between idempotent marker comments, warns if the `claude` CLI isn't installed (non-fatal — client can fall back to `--backend anthropic-api`), runs a `PARALLAX_TEST_MODE=1` smoke test. README now leads with the curl one-liner; the manual `uv tool install` path stays as a fallback for devs who already have uv.

Rejected: keychain integration (overkill for v0, adds a code path in parallax to read from `security` when env is unset); a Python-based installer (couldn't bootstrap itself before Python is set up); prompting for `ANTHROPIC_API_KEY` (only needed for the non-default backend — let clients opt in rather than prompting for a key most won't use).

**Breaks if:** `curl -LsSf .../main/scripts/install.sh | sh` on a fresh Mac doesn't land a working `parallax` on PATH, the FAL_KEY prompt hangs or silently eats input when piped through curl (the `/dev/tty` read is what prevents this — if it regresses, users type a key and the script appears frozen), re-running the installer appends a second `# >>> parallax env >>>` block instead of skipping, or the script's `set -eu` trips over the optional `read` failing when no TTY is attached (CI contexts).

## 2026-04-18 — [CHANGED] v0.1.1 patch: public repo, sonnet default, `parallax update` (tag v0.1.1)
Three small ships bundled into a patch:
- Repo flipped to public so `uv tool install git+<url>` is a true one-liner. v0 has no secrets in-repo (keys all come from the client's env), and "easy install on any Mac" is an explicit VISION principle — private + easy-install don't compose.
- `parallax update` subcommand shells out to `uv tool upgrade parallax`. Fails fast with an install hint if uv isn't on PATH. Makes install + update the same sticky-note story.
- Claude-code backend defaults to `model="sonnet"` (override via `PARALLAX_CLAUDE_MODEL`). Parallax's agent work is routine tool dispatch — Opus was wasted cost and latency.
- Also: bumped `pyproject.toml` from the stale `0.0.1` to `0.1.1` — the v0.1.0 tag shipped without updating pyproject, so `uv tool list` reported `parallax v0.0.1`. Tag + pyproject now agree.

**Breaks if:** `uv tool install --python 3.11 git+https://github.com/ianjamesburke/parallax-v0` on a fresh Mac (no auth configured) fails with a 401 (repo should be public), `parallax update` prints "uv not found" when uv IS on PATH, `parallax run` against claude-code backend shows Opus in the SDK transcript without `PARALLAX_CLAUDE_MODEL=opus` set, or `parallax --version` (when added) disagrees with the git tag.

## 2026-04-18 — [FUTURE] v0.1.0 shipped; pausing here, resuming on `0.2.0` branch
v0.1.0 tagged and released at https://github.com/ianjamesburke/parallax-v0/releases/tag/v0.1.0. Complete MVP arc shipped in one session: two backends, five-alias model ladder, real FAL integration, reference-image support on `mid` + `nano-banana`, per-call usage log, distribution verified via `uv tool install`, VISION.md + README. v0 is the still-gen primitive; the broader Parallax (HoP/Editor/Compose, manifest-first video pipeline) is explicitly not in v0 — see VISION.md.

Pausing to avoid designing v1 schema without a concrete video brief driving it — spec ambiguity is the bottleneck (per CLAUDE.md), not implementation speed. Two open next-swings when picking up on `0.2.0`:

1. **premium-ref via Flux Kontext Pro** — ~1 commit capstone on v0, completes the ref matrix. Pick this if v0.2.0 is meant as a cleanup bump on the still-gen primitive.
2. **Start v1 in a new repo** — manifest-first video pipeline. Only start if a real video brief is in hand; designing the manifest schema in the abstract is the wrong move.

If the pick is #2, `0.2.0` was the wrong branch name — v1 belongs in a separate repo per VISION.md's "v0 stays a standalone primitive that v1 consumes" framing. Reconsider before opening a PR.

## 2026-04-18 — [CHANGED] Distribution verified: `uv tool install` works
Live-tested `uv tool install --python 3.11 --from <repo> parallax --force` on this machine. Installed binary at `~/.local/bin/parallax`, runs from any cwd, produces `output/` relative to cwd, writes sessions/usage to `~/.parallax/`. Both backends verified from the installed binary in `/tmp` with `PARALLAX_TEST_MODE=1`. Minimal README added with the install one-liner, env-var table, and model-alias table.

**Breaks if:** `uv tool install` from this repo requires any manual venv setup, omitting `--python 3.11` succeeds silently against a lower Python (it shouldn't — pyproject.toml pins `requires-python = ">=3.11"`), or the installed `parallax` binary is missing from the user's PATH after install.

## 2026-04-18 — [GOTCHA] `uv tool install` needs `--python 3.11` on systems with older default Python
System Python on this machine is 3.9.6, so a bare `uv tool install --from <repo> parallax` fails with "Python>=3.11 required" since uv tries to match against the system interpreter rather than downloading one. The fix is `--python 3.11` (uv will download 3.11 if it doesn't have it). Documented in the README. Do not "fix" this by lowering `requires-python` — the SDKs and type hints we use (`str | None`, etc.) require 3.10+, and we deliberately pinned 3.11 as the install target per the project's Python-tooling convention.

## 2026-04-18 — [CHANGED] Reference-image support extended to `mid` (flux/dev img2img)
`ModelSpec` gained `ref_param_name: str | None` and `max_refs: int` so each model can declare both the edit endpoint's arg name and the cardinality. `fal.generate` dispatches: singular-param models get `args[param] = url_str`; list-param models get `args[param] = list_of_urls`. `tools._validate_refs` rejects over-the-limit refs at the tool boundary so the agent pivots before any upload happens.

Current support matrix: `mid` (flux/dev/image-to-image, `image_url`, max 1), `nano-banana` (gemini-25-flash-image/edit, `image_urls`, max 8). `draft`, `premium`, `grok` still raise — adding each is a pricing.py patch plus one live verify, no dispatch changes.

Rejected: a per-spec callable that shapes args arbitrarily. `ref_param_name` + `max_refs` covers the two shapes we've seen and will cover the Flux Kontext family too — we'll only need the callable if a future model wants more than refs (e.g. a separate `mask_url`), and that's an additive field, not a rewrite.

**Breaks if:** calling `generate_image(model="mid", reference_images=[<real jpg>])` with `FAL_KEY` set doesn't produce a file in `output/`, or passes `image_urls` (plural) to `fal-ai/flux/dev/image-to-image` — that endpoint only knows `image_url` and will silently ignore or error on an unknown field.

## 2026-04-18 — [CHANGED] Reference-image support (nano-banana only for v0)
`generate_image` gains an optional `reference_images: list[str]` of local paths. When supplied, we upload each via `fal_client.upload_file`, route to the model's `edit_fal_id` sibling endpoint, and pass the returned URLs as `image_urls`. Only nano-banana (`fal-ai/gemini-25-flash-image/edit`) supports this in v0 — Flux Kontext/redux/img2img and a grok edit path are per-endpoint work we'll add one at a time as demand shows up, not speculatively. Adding them later is purely additive: set `edit_fal_id` on the spec and the dispatch just works.

Unsupported-model + missing-file validation lives at the tool boundary (`tools._validate_refs`) so failures surface as `tool_result is_error=true`, not crashes. Live verify confirmed: the agent, told to use `draft` with refs, received the ValueError, stopped retrying, and correctly pivoted to recommending nano-banana. `alias_guidance()` now tells the model which aliases support refs and instructs "use one of those when the user supplies input images."

Rejected: generic "pass any FAL param through the tool" — same footgun as model IDs. Rejected for v0: Flux Kontext on `premium` — endpoint + param shape differ from nano-banana's `image_urls`, would expand the test matrix before we know which cases matter.

**Breaks if:** calling `generate_image(model="nano-banana", reference_images=[<real png>])` with `FAL_KEY` set doesn't produce an output file; or calling it with `model="draft"` plus refs doesn't raise a ValueError at the tool boundary before any FAL call.

## 2026-04-18 — [CHANGED] Real FAL integration wired (prompt-only, v0)
`generate_image` now calls FAL for real via `fal_client.subscribe` when not in test mode. `fal.generate(prompt, spec)` is the only call site; runner + downloader are dependency-injectable so hermetic tests don't touch the SDK. FAL-side failures (auth, quota, outage, safety) wrap as `RuntimeError` and surface to the model as `tool_result` errors — the agent can retry, apologize, or pivot instead of crashing the loop. `FAL_KEY` required; missing key fails fast at first real call. Deliberately prompt-only for v0 — reference-image support is purely additive at the tool-schema / pricing surface when it lands; the only non-additive work will be stripping prior-turn input bytes at the resume boundary (already flagged in an earlier DECISION entry).

Rejected: raw HTTP (reimplements queueing/polling for zero benefit); passing arbitrary FAL params through the tool schema (invites the "agent hallucinates a field" footgun we already guarded against for model IDs).

**Breaks if:** `FAL_KEY=... PARALLAX_TEST_MODE=` (unset) `uv run parallax run --backend anthropic-api --brief "..."` doesn't produce a real image file in `output/` whose extension matches the FAL-returned content type, or the usage record for that call shows `test_mode: true` / `cost_usd: 0.0`.

## 2026-04-18 — [GOTCHA] FAL returns JPEG from Flux Schnell — honor URL extension, don't assume .png
First live verify wrote a file named `draft_<hash>.png` that was actually JPEG content (Flux Schnell defaults to JPEG on FAL). Fixed by deriving the extension from the returned URL path (`.png/.jpg/.jpeg/.webp` allowed, else fallback `.png`). Don't hardcode an extension — different FAL models return different formats (and even the same model can change its default). Viewers coped, but `file` command revealed the mismatch, and downstream tools that dispatch by extension would have silently misbehaved.

## 2026-04-18 — [CHANGED] Model alias ladder + per-call cost/time tracking
Five-alias ladder (`draft`, `mid`, `premium`, `nano-banana`, `grok`) defined in `src/parallax/pricing.py`, verified against fal.ai on 2026-04-17. Agent-facing tool schema constrains `model` to `enum: list(ALIASES)`; unknown alias raises `ValueError`. System prompt carries `alias_guidance()` so the model sees descriptions + prices and knows to default to `mid`. Every `generate_image` call appends one NDJSON line to `~/.parallax/usage.ndjson` with `{ts, session_id, backend, alias, fal_id, tier, prompt_preview, output_path, duration_ms, cost_usd, test_mode}`. New `parallax usage [--include-test]` subcommand aggregates by alias and session. Test-mode records land with `cost_usd=0.0` and `test_mode=true` so you can see what a dry run would have cost without polluting real-spend totals.

Live verify caught a real bug: the MCP tool handler on the claude-code backend runs in a separate task context from the SDK message loop, so `current_session_id.set(sid)` on the outer loop did not propagate in — usage records landed with `session_id=None` on that backend. Fixed by capturing a mutable `sid_holder` dict in the tool closure and re-setting the ContextVar inside the handler. Anthropic-api backend was unaffected (single task). Lesson reinforced: ContextVars are per-task; closures over SDK-owned tasks must re-set at the boundary.

**Breaks if:** `parallax usage --include-test` after a `PARALLAX_TEST_MODE=1 parallax run` shows `0 sessions` or a `None` session_id when inspecting `~/.parallax/usage.ndjson` directly. Also breaks if passing an unknown alias (e.g. `"flux-pro"`) in a brief succeeds instead of erroring at the tool boundary.

## 2026-04-17 — [CHANGED] Runtime logging wired; default WARNING, -v INFO, -vv DEBUG
Added `src/parallax/log.py` and a `-v/-vv` flag on the CLI (also `PARALLAX_LOG_LEVEL` env). At INFO you see: backend selected, session id + session-log path, each tool call with a truncated args summary, each tool result, and — on the claude-code path — the SDK's jsonl transcript path so you know exactly where to grep when behavior is off. DEBUG adds full args, full raw results, and per-SDK-message type.

Caught via live invocation that I should have done before calling commit 1b shipped: both backends' hermetic tests were green, but neither had actually been executed end-to-end against real infra. Fixed by running test-mode `parallax run` on both backends before landing this commit. Verified: claude-code produces a real `~/.claude/projects/<sanitized-cwd>/<sid>.jsonl`, anthropic-api produces `~/.parallax/sessions/<sid>.ndjson`, and the logged SDK transcript path matches the actual file location.

**Breaks if:** `parallax -v run --backend <x> --brief "..."` with `PARALLAX_TEST_MODE=1` emits no `parallax.tools: tool call` line on stderr, or the `parallax.backends.claude_code: SDK transcript` path doesn't correspond to an existing file on disk after the run.

## 2026-04-17 — [GOTCHA] Hermetic tests are not enough — run the CLI live before claiming done
Both backends had passing hermetic tests (FakeAnthropic / injected async query_fn) but neither had been executed end-to-end against the real Anthropic API / real claude-agent-sdk until the user asked "did you run it yourself?". Reading the code and green unit tests is not the same as proving the wiring works. Going forward: every backend-touching commit must include a `PARALLAX_TEST_MODE=1 uv run parallax run --backend <each>` invocation before claiming the commit is shipped. Test shim keeps this cheap — no FAL, no spend — so there is no excuse.

## 2026-04-17 — [CHANGED] Two backends behind a dispatcher; default = Claude subscription
Added the `claude-code` backend (default) via `claude-agent-sdk`, which routes through the user's `claude` CLI and uses their subscription — no API key, no extra billing. The existing anthropic-api backend is still selectable via `--backend anthropic-api` or `PARALLAX_BACKEND=anthropic-api` for CI / non-subscription use. Dispatcher lives in `src/parallax/backends/__init__.py`; selection is explicit-arg > env > default, with fail-fast `check_available()` on the selected backend (claude CLI on PATH / ANTHROPIC_API_KEY set). No silent fallback.

The two backends have distinct session models: claude-code uses the SDK's native session resume (stored under `~/.claude/projects/`); anthropic-api uses our NDJSON store at `~/.parallax/sessions/`. Session IDs are opaque strings round-tripped by the CLI — they never mix. Rejected: unified `Backend` protocol with a single `run_turn()` method — the SDKs' loop shapes are too different (stateless `messages.create` vs. stateful async iterator over SDK events) for the abstraction to earn its keep. Two explicit backend modules + a tiny dispatcher beats a leaky interface.

The `generate_image` tool is exposed to claude-code via an in-process MCP server (`create_sdk_mcp_server`); both backends ultimately call the same `tools.dispatch_tool()`, so the Pillow test shim works identically on both paths.

## 2026-04-17 — [DECISION] Flat single-agent architecture wrapping anthropic SDK
Rejected orchestrator+subagents (Anthropic Agent SDK) for v0 — ~15x token cost per equivalent task and zero benefit until the pipeline branches or images exceed a single context window. Rejected literal `claude -p` subprocess wrapping — would require standing up an MCP server just to expose one tool, which defeats the "smallest possible" scope. The anthropic SDK's native tool_use + `cache_control` primitives give the same capability in ~100 lines with no hidden state and a loop the user can read top-to-bottom. The "claude -p wrapper" framing is honored architecturally (thin, transparent, skill-extensible) without the literal subprocess overhead.

## 2026-04-17 — [DECISION] Tool returns file paths; image bytes never enter agent context
The `generate_image` tool returns a string filesystem path — never bytes. This makes context discipline structural rather than dependent on future stripping logic: resumed sessions stay cache-warm by construction. When Phase 2 adds `reference_images` (an input, not an output), we will add explicit byte-stripping of prior-turn inputs at the resume boundary.

## 2026-04-17 — [DECISION] Session state as append-only NDJSON at ~/.parallax/sessions/
Global location chosen by the user. One NDJSON file per session; each line is a discrete event (`session_start`, `user_message`, `assistant_message`, `tool_result`, `session_resumed`, `session_end`). Resume reconstructs the messages array by walking the file. Rejected: SQLite (overkill), JSON blob rewritten per turn (loses partial-failure forensics), per-session directory (premature). `PARALLAX_SESSIONS_DIR` env var overrides the location for tests.

## 2026-04-17 — [DECISION] Test mode as a first-class runtime flag
`PARALLAX_TEST_MODE=1` swaps any external generator for a Pillow shim that renders the request parameters as readable text onto a 1024x1024 PNG. The PNG IS the receipt of exactly what the agent asked for — zero network, zero spend, fully transparent. The filename is a deterministic hash of (prompt, model) so repeated calls produce stable paths, which makes downstream test assertions cleaner. This ships in commit 1, not as future work.

## 2026-04-17 — [DECISION] generate_image tool schema is minimal for commit 1
Schema is `{prompt, model}` only. `reference_images` and `count` land in Phase 2 commits with their own tests. Keeping commit 1's tool schema minimal proves the loop wiring without entangling it with image-routing logic or multi-output bookkeeping.

## 2026-04-17 — [GOTCHA] pyright sees src-layout imports as unresolved before `uv sync`
During file creation, pyright flagged `from .sessions import …` etc. as unresolvable. They resolve correctly after `uv sync` publishes the package into the venv via the editable `parallax` install. The src-layout with `[tool.hatch.build.targets.wheel] packages = ["src/parallax"]` is correct — do not flatten the layout.

## 2026-04-17 — [FUTURE] Real FAL client lands in commit 2
`generate_image` raises `NotImplementedError` when `PARALLAX_TEST_MODE` is unset, so the only currently-usable path is the shim. Phase 2, commit 1: add the real FAL client behind the existing schema. Phase 2 subsequent commits: `reference_images`, `count`, character consistency, and context discipline for input images.
