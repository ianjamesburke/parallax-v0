# Parallax — 24-Hour Refactor Plan

**Goal:** Stable, agentically-driven content pipeline. Two clean repos. OpenRouter-backed CLI. Self-testing loop. Done = full lifecycle simulation passes end-to-end with no manual intervention.

---

## Architecture

Two repos, one dependency arrow.

### Repo 1 — `parallax` (this repo, refactor on worktree branch)
The media CLI. Stateless. No knowledge of sheets, Drive, or Frame.io.
- Unified OpenRouter backend for image / video / TTS.
- ElevenLabs as direct escape hatch for brand-locked voices (`voice: eleven:<id>`).
- ffmpeg post-prod stays (trim_silence, key_avatar_track, ken_burns, captions, headline).
- CLI surface unchanged: `parallax produce`, `parallax audio detect-silences`, `parallax audio trim`.
- Test mode generates prompt-stub assets, never calls a real model.

### Repo 2 — `narrative-parallax` (new repo, OUT of Drive folder)
The orchestrator agent. Owns sheet, Drive, Frame.io, lifecycle.
- Pydantic-AI agent loop on OpenRouter (text/reasoning).
- Tools: subprocess `parallax produce`, Drive helpers, Sheet helpers, Frame.io helpers.
- One `config.yaml`: drive_root, sheet_id, frameio_project_id, env-var names.
- `concepts.json` schema preserved; only status taxonomy changes.
- Drive folder stays the data layer (concepts.json, brand folders, ref assets). Code never lives there.

**The seam:** narrative-parallax shells out to `parallax` as a subprocess. No Python imports across the boundary. CLI version pinned in config.

---

## Status State Machine (replaces `ready_for_work` overload)

| Status | Meaning | Owner of next transition |
|---|---|---|
| `awaiting_brief` | Row exists, no instructions yet | Human |
| `script_pending` | Agent generating script | Agent |
| `script_needs_approval` | Script written, awaiting human sign-off | Human |
| `stills_pending` | Agent generating stills | Agent |
| `stills_needs_approval` | Stills generated, awaiting sign-off | Human |
| `video_pending` | Agent rendering | Agent |
| `video_needs_approval` | Uploaded to Frame.io, awaiting review | Human |
| `revisions_pending` | Pulling Frame.io comments | Agent |
| `blocked_assets` | Drive missing refs — see Needs column | Human |
| `blocked_decision` | Agent stuck on ambiguity | Human |
| `approved` | Client signed off | Terminal |
| `delivered` | Final exported / sent | Terminal |
| `rejected` | Abandoned | Terminal |

**Human advances by setting status to `*_pending` (kicks agent back into work) or `approved` (advances stage).**

### New sheet column: `Needs`
One agent-written sentence stating exactly what's blocking. Surfaces alongside Status.
- "Awaiting script approval — review: <doc-link>"
- "Missing refs: hero.png, product.png — upload to <drive-link>"
- "Frame.io comments unresolved — clarify scene 3"

The user scans Status + Needs and knows exactly what to do. No ambiguity.

---

## Plan.yaml Schema Additions (future-proofing)

Add optional per-scene timing override fields. Null = current behavior (compute from VO).

```yaml
scenes:
  - index: 0
    vo_text: "..."
    prompt: "..."
    duration_s: null         # explicit override; null = VO-derived
    start_offset_s: null
    fade_in_s: null
    fade_out_s: null
```

Renderer ignores when null. Future graphical editor writes here. Zero cost now, zero rework later.

### `voice` field becomes free-text
- Free-text character description → Gemini 3.1 Flash TTS style prompt (default path).
- `eleven:<voice_id>` → ElevenLabs direct (escape hatch for brand-locked voices).

---

## Workstream 1 — Parallax CLI Refactor (worktree branch)

- [ ] **`openrouter.py` module.** Unified client. Handles image, video, TTS through one interface. Per-alias model + fallback chain config.
- [ ] **Alias table rewrite in `pricing.py`.** Image: `draft`, `mid`, `premium` (Nano Banana Pro), `nano-banana`, `seedream`. Video: `kling`, `veo`, `seedance`, `wan`, `sora`. TTS: `gemini-flash-tts` (default), `gpt-4o-mini-tts`, `voxtral`. Each entry: model_id, fallback_alias, cost.
- [ ] **Rip out FAL-direct.** `fal.py` → deleted. All routes go through `openrouter.py`. ElevenLabs direct retained as `voice: eleven:<id>` escape hatch only.
- [ ] **Add timing-override fields** to plan.yaml parser in `produce.py`. Honor `duration_s`, `start_offset_s` if present; trim/pad scene logic documented.
- [ ] **Test stub generators.**
  - Image: PIL-rendered 1080×1920 PNG with prompt text on colored bg.
  - Video: ffmpeg drawtext mp4, 5s, prompt text.
  - TTS: sine-wave wav, duration = wpm-derived from text length.
  - All write `cost_usd: 0.0`, `test_mode: true` to usage log.
- [ ] **Pixel-perfect file logging.** `~/.parallax/logs/<run_id>.log`, full debug always-on, one JSON event per line. Every external call logs request, response, duration_ms, cost. `parallax tail <run_id>` reads it.
- [ ] **Test suite.** Coverage target: every alias × every operation × test-mode + real-mode mock. Failing-fallback simulation. Plan-to-manifest invariants. Run via `uv run pytest`.

### Verify before merge
- `PARALLAX_TEST_MODE=1 parallax produce --folder fixtures/test_concept --plan plan.yaml` produces an mp4 in <5s, all stubs.
- `parallax tail <run_id>` shows complete trace.
- All existing dev-log "Breaks if" lines still pass.

---

## Workstream 2 — narrative-parallax Repo Bootstrap

- [ ] **Create repo `~/Documents/GitHub/narrative-parallax`.** git init, pyproject.toml (`requires-python = ">=3.11"`), uv setup.
- [ ] **Move scripts out of Drive folder.** Copy `tick.py`, `concept_store.py`, `sheets_sync.py`, `intake_from_sheet.py`, `frameio_revisions.py`, `process_docx.py`, `run_render.py`, `upload_to_frameio.py`, `mark_*.py`, `models.py`, `drive_layer.py`, `preflight.py` into the new repo. Update imports.
- [ ] **`config.yaml`** at repo root:
  ```yaml
  drive_root: "/Users/ianburke/Library/CloudStorage/.../PARALLAX CONTENT"
  sheet_id: "1q8PxEj..."
  frameio_project_id: "e8807083-..."
  parallax_cli_version: "0.1.4"   # pinned subprocess dep
  env_keys:
    openrouter: OPENROUTER_API_KEY
    elevenlabs: ELEVENLABS_API_KEY
    google_service_account: GOOGLE_APPLICATION_CREDENTIALS
    frameio: FRAMEIO_TOKEN
  ```
- [ ] **Replace `run_render.py` shell-out** with Pydantic-AI agent loop on OpenRouter. Tools: `parallax_produce`, `read_drive`, `write_drive`, `update_sheet`, `upload_frameio`, `pull_frameio_comments`, `mark_blocked`, `mark_needs_approval`.
- [ ] **New status state machine** wired in `tick.py` and `concept_store.py`. Each transition has its own handler function. No more universal `ready_for_work` branch-on-frameio_link.
- [ ] **Drive readiness gate** moves out of agent prompt into deterministic Python. `preflight.py` enumerates all assets referenced in `instructions.md`, verifies presence + size > 0, file type matches expected. Mismatch → `blocked_assets` + Needs column populated. Agent never starts work on incomplete asset set.
- [ ] **`Needs` column** added to sheet schema. Update `sheets_init.py`, `sheets_sync.py`. `concepts.json` gets a `needs` field, agent-writable, surfaces in sheet on every sync.
- [ ] **Script generation stage.** New agent step: takes `instructions.md` + brand context → writes script Google Doc → flips status to `script_needs_approval`, sets `review_link`. `create_script_doc.py` adapted.
- [ ] **Per-concept log directory.** `logs/<concept_id>/<run_id>.log`. Sheet's Log column shows tail; file has full trace.
- [ ] **Simulation mode.** `NARRATIVE_TEST_MODE=1`:
  - In-memory dict mocks Sheets API.
  - Local `fixtures/drive/` folder mocks Drive client.
  - Fake Frame.io URLs.
  - Subprocess `parallax` runs with `PARALLAX_TEST_MODE=1`.
  - Full lifecycle from intake to delivery in seconds, no API calls.

### Verify before declaring stable
- `NARRATIVE_TEST_MODE=1 narrative tick` runs the full lifecycle on three fixture concepts back-to-back.
- Every status transition is exercised by a test.
- Every blocker condition (missing refs, ambiguous script source, Frame.io revision pull) has a test.
- Drive race scenarios (ref appears mid-tick, ref missing, ref invalid format) all surface as `blocked_assets` with correct Needs message.

---

## Workstream 3 — Test Infrastructure (cross-repo)

- [ ] **`fixtures/` directory in narrative-parallax.** Three sample concepts at different stages, fake brand configs, mock Drive folder layout.
- [ ] **`make test` / `just test`** at each repo root: runs full unit + integration + simulation suite. Target: <60s end-to-end.
- [ ] **CI hook (local).** Pre-commit runs the simulation suite. Failing test = no commit.

---

## Sequencing

**Block 1 — CLI refactor (parallax-v0 worktree)**
1. Create worktree branch.
2. Build `openrouter.py` + alias rewrite.
3. Add test stub generators + file logging.
4. Add plan.yaml timing-override fields.
5. Pass full test suite in test mode.
6. Smoke test one real render (single concept).

**Block 2 — narrative-parallax bootstrap**
1. Create repo, move scripts, wire config.yaml.
2. Implement new status state machine.
3. Move Drive readiness gate to deterministic Python.
4. Add Needs column.
5. Add script generation stage.

**Block 3 — Agent loop replacement**
1. Pydantic-AI agent + tool definitions.
2. Wire OpenRouter for text/reasoning.
3. Replace `run_render.py` shell-out.

**Block 4 — Simulation harness**
1. Mock Sheets/Drive/Frame.io clients.
2. Three-concept fixture lifecycle test.
3. Pass full simulation, end-to-end.

**Block 5 — Live verification**
1. One real concept end-to-end on staging sheet.
2. Confirm Drive race gate triggers correctly with deliberate missing ref.
3. Cost-per-run measured against $0.20 target.

---

## Out of scope (do not build now)

- Plexi UI / graphical editor (schema future-proofed, build deferred)
- JS-spliced live preview
- Team rollout / multi-user auth
- Manifest.yaml round-trip editing (manifest stays receipt-only)
- B-roll / footage analysis pipeline
- Full module-extraction of `tools_video.py` (touch-only-when-edited per existing dev log)

---

## Done = condition

1. `PARALLAX_TEST_MODE=1` full produce pipeline → mp4 stub in <5s.
2. `NARRATIVE_TEST_MODE=1 narrative tick` → three-concept fixture lifecycle passes.
3. One real concept on staging sheet, kickoff to delivery, no manual intervention beyond approval clicks.
4. Drive race gate triggers `blocked_assets` correctly when ref missing.
5. Cost-per-run < $0.20 average measured across the three test concepts.
6. `parallax tail <run_id>` and `narrative tail <concept_id>` give pixel-perfect debug trace.
