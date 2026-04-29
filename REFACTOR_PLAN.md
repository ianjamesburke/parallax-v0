# Parallax — Unified Refactor Plan

**Status:** Active. Updated 2026-04-29.
**Goal:** Produce videos conversationally through Claude Code using a clean Parallax CLI + skills stack. One unified provider (OpenRouter), one billing surface, one models catalog. Four layers, each with one job.

---

## Latest decisions (2026-04-29)

What changed since the prior cut of this plan:

1. **Single provider: OpenRouter only.** All image, video, TTS, and LLM calls go through OpenRouter. **fal removed entirely. ElevenLabs removed entirely** (no escape hatch — Gemini TTS is the default, and OpenRouter routes Gemini TTS).
2. **One env var: `OPENROUTER_API_KEY`.** Frame.io / Drive / Sheets keys remain but are confined to the orchestrator (Layer 4).
3. **`brief.yaml` is a first-class CLI primitive**, not a narrative-specific artifact. Generic Parallax authors and consumes brief.yaml. Narrative just provides templates with brand defaults.
4. **Four layers, not two repos.** CLI + generic skill (Layer 1–2) + narrative skill + narrative orchestrator (Layer 3–4). The CLI/skill symlink is removed; the skill becomes its own repo.
5. **`pricing.py` is replaced by a `models/` catalog** — pricing + capabilities (start/end frame, aspect ratios) + input requirements (refs, style, product) + voice list. Three tiers everywhere: `draft` / `mid` / `premium`.
6. **Aspect ratio is first-class.** Every command accepts `--aspect`. No more hardcoded 9:16.
7. **New CLI primitives:** `parallax ingest` (footage analysis → searchable clip index), `parallax plan` (brief.yaml → plan.yaml).
8. **V2 namespace adopted** (see Layer 1 below).
9. **Skill terseness rule.** Generic Parallax skill enforces 1–3 sentence agent responses.
10. **Legacy `PARALLAX CONTENT/scripts/` is on the kill list** — replaced by Layer 4 orchestrator. See Appendix B.

---

## Architecture — four layers

| Layer | Repo | Role |
|---|---|---|
| **1. `parallax` CLI** | `parallax-v0` (de-symlinked from skill) | Pure tool. brief.yaml + plan.yaml inputs. OpenRouter-only provider router. Models catalog. V2 namespace. |
| **2. Generic `parallax` skill** | New repo, e.g. `~/Documents/GitHub/parallax-skill` | Terse playbook for driving the CLI conversationally. Knows brief.yaml format. |
| **3. `narrative-parallax` skill** | Inside `narrative-parallax` repo | Thin specialization: brand presets, voice defaults, brief.yaml templates, concepts.json + Frame.io conventions. |
| **4. `narrative-parallax` orchestrator** | Same repo as Layer 3 | Folder watcher + Sheets sync + Frame.io upload. Replaces `PARALLAX CONTENT/scripts/`. Implements Kimi plan. |

**The seam:** every layer talks to the layer below it via the CLI subprocess. No Python imports across layers. CLI version pinned in orchestrator config.

---

## Build order

1. **Layer 1 first** — everything depends on a stable CLI.
2. **Layer 2** — extract generic skill once Layer 1 contracts are firm.
3. **Layer 3** — narrative skill on top of generic.
4. **Layer 4** — orchestrator (this is the existing `KIMI_PLAN.md` work; cross-link, don't duplicate).
5. **Kill list** — delete legacy scripts only after Layer 4 is live.

---

## Layer 1 — Parallax CLI refactor `[INCOMPLETE]`

Worktree: `worktrees/refactor-v3` (new branch off latest main).

### Phase 1.1 — `models/` catalog replaces `pricing.py` `[INCOMPLETE]`
**Description.** Replace `src/parallax/pricing.py` with `src/parallax/models/` package. One YAML or Python file per modality (`image.yaml`, `video.yaml`, `tts.yaml`). Each entry: `alias`, `provider_model_id` (OpenRouter ID), `tier` (`draft`/`mid`/`premium`), `cost_per_unit`, `unit` (`image`/`second`/`char`), `capabilities` (start_frame, end_frame, aspect_ratios), `inputs` (style_ref, product_ref, character_ref). TTS catalog includes the full Gemini voice list.
**Acceptance.** `parallax models list` prints the full catalog. `parallax models show <alias>` prints capabilities. Every existing CLI call resolves via the catalog. Old `pricing.py` is deleted.
**Breaks if:** `parallax models list` is empty or any tier is missing.

### Phase 1.2 — OpenRouter-only provider layer `[INCOMPLETE]`
**Description.** New module `src/parallax/provider/openrouter.py` is the only provider. One client, one auth path (`OPENROUTER_API_KEY`). Routes by modality using the models catalog. Delete `src/parallax/fal.py` and any ElevenLabs imports. Fail fast on missing key with explicit message naming `OPENROUTER_API_KEY`.
**Acceptance.** `grep -r "fal\|elevenlabs\|ELEVENLABS" src/` returns zero hits. A real-mode produce run with only `OPENROUTER_API_KEY` set succeeds end-to-end on a test concept.
**Breaks if:** any env var other than `OPENROUTER_API_KEY` (and Google service-account vars used by Layer 4) is required for a CLI-only run.

### Phase 1.3 — Aspect ratio is first-class `[INCOMPLETE]`
**Description.** Every `generate` and `produce` command accepts `--aspect <ratio>` (e.g. `9:16`, `16:9`, `1:1`). brief.yaml carries top-level `aspect:`; plan.yaml inherits unless scene overrides. No hardcoded `1080x1920` in code paths — derive from aspect + working resolution.
**Acceptance.** `parallax produce --aspect 16:9 ...` produces a 16:9 mp4. Test fixtures cover 9:16, 16:9, 1:1.
**Breaks if:** any rendered output is the wrong aspect, or any ffmpeg invocation contains a hardcoded resolution.

### Phase 1.4 — `brief.yaml` as first-class input `[INCOMPLETE]`
**Description.** Pydantic `Brief` model. Fields: `goal`, `aspect`, `voice`, `success_criteria`, `assets.required` (provided + generated inventory), `script.scenes`. `brief.validate_assets(folder)` checks required asset presence. Loadable via `parallax produce --brief brief.yaml` (skips planning) or as input to `parallax plan`.
**Acceptance.** Pytest suite: valid brief parses; missing provided asset raises with explicit path; missing generated asset is fine. Live: convert one concept (e.g. `0018-alpha-lion`) to brief.yaml end-to-end.
**Breaks if:** `parallax produce --brief ...` requires extra flags beyond `--folder` and the brief.

### Phase 1.5 — `parallax plan` command `[INCOMPLETE]`
**Description.** New command. Reads `brief.yaml`, validates assets, LLM-generates per-scene prompts for `source: generated` items, writes `plan.yaml`. Emits `questions.yaml` + non-zero exit if gaps detected. Same alias system as `produce`.
**Acceptance.** `parallax plan --folder fixtures/test_001` produces a valid `plan.yaml`. Missing provided asset → `questions.yaml` written, exit 1. Resume after asset added → success.
**Breaks if:** plan.yaml is generated when assets are missing, or questions.yaml is written silently with no log line.

### Phase 1.6 — `parallax ingest` command `[INCOMPLETE]`
**Description.** New command. Accepts a path or directory of footage. For each clip: duration, transcript via Whisper (or Gemini audio), optional `--visual` for Gemini visual analysis at sampled timestamps. Writes a searchable clip index to `<folder>/index.json`. Supports `--estimate` (dry-run cost report).
**Acceptance.** `parallax ingest path/to/clips/ --estimate` prints duration + cost without API calls. Without `--estimate`, writes index with timestamped transcript and (if `--visual`) per-scene tags.
**Breaks if:** ingest runs synchronously on a directory of >5 clips (must parallelize).

### Phase 1.7 — V2 namespace `[INCOMPLETE]`
**Description.** Adopt the V2 command tree:
```
parallax ingest      <path|dir> [--visual] [--estimate]
parallax generate    still | video | voice | music
parallax script      write | rewrite
parallax plan        --folder <path>
parallax produce     --folder <path> [--brief|--plan]
parallax edit        <instruction>
parallax compose     # Ken Burns assembly + captions + headline
parallax trim        <file>
parallax models      list | show <alias>
parallax setup       # one-time OpenRouter key
parallax status
parallax tail        <run_id>
parallax web         # local UI (deferred)
parallax project     new | list
parallax publish     # YouTube uploader (V3 stub)
```
**Acceptance.** `parallax --help` lists exactly this tree. Removed: `animate`, `run`, `create`, `voiceover`, `transcribe`, `align`. Migration map in `MIGRATION.md`.
**Breaks if:** any old V1 command name still resolves.

### Phase 1.8 — Skill responses convention `[INCOMPLETE]`
**Description.** Add `--quiet` / `--json` first-class output mode. Default human output is one-liner per phase ("✓ Generated still 3/8 — $0.04"). Full debug stays in `~/.parallax/logs/<run_id>.log`. The convention is enforced by the skill (Layer 2), but the CLI must support it cleanly.
**Acceptance.** `parallax produce ... --quiet` emits ≤ N lines per phase. `--json` emits one NDJSON event per state change.
**Breaks if:** any subcommand prints more than one line per logical step in `--quiet` mode.

### Phase 1.9 — Provider migration verify `[INCOMPLETE]`
**Description.** Single end-to-end real-mode run on one concept (`0018-alpha-lion`) with only `OPENROUTER_API_KEY` set. Image, video, TTS all routed through OpenRouter. No fal, no ElevenLabs.
**Acceptance.** Final mp4 produced. Cost log shows OpenRouter as sole provider. Cost-per-run < $0.30 target.
**Breaks if:** any OpenRouter call fails over to a deleted fal/ElevenLabs path (those paths must be gone, not fall-through).

---

## Layer 2 — Generic Parallax skill `[INCOMPLETE]`

New repo: `~/Documents/GitHub/parallax-skill`. Symlink into `~/.claude/skills/parallax/`. The current 41K SKILL.md in `parallax-v0` is the source material to slice.

### Phase 2.1 — Bootstrap repo `[INCOMPLETE]`
**Description.** Create repo. Add `SKILL.md` (target ≤ 3K), `CLAUDE.md`, `README.md`. Symlink to `~/.claude/skills/parallax`. Remove the existing symlink that points into `parallax-v0`.
**Acceptance.** `ls -la ~/.claude/skills/parallax` resolves to the new repo. `parallax-v0` is no longer symlinked.

### Phase 2.2 — Slice generic content `[INCOMPLETE]`
**Description.** From the 41K SKILL.md, extract: brief.yaml authoring, plan.yaml iteration rules, model alias selection, locking conventions (`still_path`/`audio_path`/`words_path`), aspect ratio guidance, response terseness rule. Leave OUT: concepts.json, Frame.io, Drive, Sheets, brand presets — those move to Layer 3.
**Acceptance.** SKILL.md ≤ 3K. A fresh agent can author a brief.yaml + run `parallax plan` + `parallax produce` from scratch using only the skill.
**Breaks if:** SKILL.md mentions concepts.json, frameio, sheets, or any narrative-specific brand.

### Phase 2.3 — Terseness convention `[INCOMPLETE]`
**Description.** Add explicit rule: "Responses are 1–3 sentences. No analysis preamble. No multi-paragraph wrap-ups. Show generated assets and ask one short question."
**Acceptance.** Manual test: drive one full concept through Claude Code with this skill. Every assistant turn fits in 3 sentences unless the user asks for detail.

---

## Layer 3 — Narrative Parallax skill `[INCOMPLETE]`

Lives inside `narrative-parallax` repo at `skills/narrative-parallax/SKILL.md`. Symlinked into `~/.claude/skills/narrative-parallax/`.

### Phase 3.1 — Bootstrap skill `[INCOMPLETE]`
**Description.** Create skill. References Layer 2 generic skill ("read parallax skill first"). Owns: brand presets (logos, fonts, voices per brand), brief.yaml templates with Narrative defaults, concepts.json conventions, Frame.io upload + revision flow, status state machine.
**Acceptance.** A fresh agent given only the narrative skill + the orchestrator config can pick up a `ready_for_work` concept and ship to Frame.io.

### Phase 3.2 — Brand presets file `[INCOMPLETE]`
**Description.** `narrative-parallax/brands.yaml` (already exists from Layer 4 prep). Schema: per-brand voice alias, default aspect, brand color palette, default model tier. brief.yaml templates pull from this.
**Acceptance.** New brand added via `narrative brands add <name>` + sheet intake.

---

## Layer 4 — narrative-parallax orchestrator (Kimi plan) `[INCOMPLETE]`

Authoritative plan: `~/Documents/GitHub/narrative-parallax/KIMI_PLAN.md`. **Do not duplicate phases here.** This section is the cross-reference + a few invariants that touch the CLI seam.

### Phase 4.1 — Status state machine `[INCOMPLETE]`
Replaces `ready_for_work` overload with explicit per-stage statuses (see prior cut of this plan for the table). Sheet `Needs` column is agent-written, surfaces blocker.

### Phase 4.2 — Subprocess seam `[INCOMPLETE]`
Orchestrator shells out to `parallax produce`, `parallax plan`, `parallax ingest`. Pin CLI version in `config.yaml`. No Python imports across the boundary.
**Breaks if:** any orchestrator file imports from `parallax.*` directly.

### Phase 4.3 — Drive readiness gate `[INCOMPLETE]`
Deterministic Python preflight (not agent prompt). Enumerate assets in `instructions.md`/`brief.yaml`, verify presence + non-zero size + matching mime. Mismatch → `blocked_assets` + `Needs` populated.

### Phase 4.4 — Simulation harness `[INCOMPLETE]`
`NARRATIVE_TEST_MODE=1 narrative tick` runs three fixture concepts end-to-end with mocked Sheets/Drive/Frame.io. Subprocess `parallax` runs in `PARALLAX_TEST_MODE=1`. Full lifecycle in seconds, zero API calls.

---

## Done = condition

1. Layer 1: `PARALLAX_TEST_MODE=1 parallax produce --brief fixtures/test_concept/brief.yaml` produces an mp4 stub in <5s. Real-mode run with `OPENROUTER_API_KEY` only, on `0018-alpha-lion`, ships an mp4. No fal/ElevenLabs imports anywhere.
2. Layer 2: generic skill ≤ 3K, drives a clean concept end-to-end through Claude Code with terse responses.
3. Layer 3: narrative skill ships one Narrative concept end-to-end including brand preset application.
4. Layer 4: `NARRATIVE_TEST_MODE=1 narrative tick` passes three-concept fixture lifecycle. One real concept on staging sheet kickoff → delivery with no manual intervention beyond approval clicks.
5. Kill list (Appendix B): every legacy script in `PARALLAX CONTENT/scripts/` is either migrated into the orchestrator repo or deleted.
6. `parallax tail <run_id>` and `narrative tail <concept_id>` give pixel-perfect debug trace.

---

## Out of scope (explicit, do not build now)

- Plexi UI / graphical editor (schema future-proofed, build deferred)
- Multi-user auth / team rollout
- Manifest.yaml round-trip editing (manifest stays receipt-only)
- B-roll semantic indexing pipeline beyond `parallax ingest` MVP
- Full module-extraction of `tools_video.py` (touch-only-when-edited per CLAUDE.md)
- YouTube publish (V3, namespace reserved only)

---

## Appendix A — Provider migration map

Every current call site → OpenRouter equivalent. Anything still pointing at fal or ElevenLabs at the end of Layer 1 is a Phase 1.2 regression.

| Current | New |
|---|---|
| `fal.run("fal-ai/flux/dev", ...)` | `openrouter.image(alias="mid", ...)` |
| `fal.run("fal-ai/nano-banana", ...)` | `openrouter.image(alias="nano-banana", ...)` |
| `fal.run("fal-ai/seedance/...", ...)` | `openrouter.video(alias="seedance", ...)` |
| `fal.run("fal-ai/kling/...", ...)` | `openrouter.video(alias="kling", ...)` |
| `fal.run("fal-ai/veo/...", ...)` | `openrouter.video(alias="veo", ...)` |
| `elevenlabs.generate(voice_id=...)` | `openrouter.tts(alias="gemini-flash-tts", voice=<gemini-voice>, ...)` |
| Anthropic SDK direct (text) | `openrouter.chat(model=...)` |

Voice migration: every brand currently using an ElevenLabs voice gets remapped to a Gemini TTS voice in `brands.yaml`. Document the mapping when migrating.

---

## Appendix B — Kill list

Files / dirs to delete or migrate. **Order matters** — only delete each item once the listed unblock condition is met.

| Path | Action | Unblocked by |
|---|---|---|
| `parallax-v0/src/parallax/pricing.py` | Delete | Phase 1.1 (`models/` catalog live) |
| `parallax-v0/src/parallax/fal.py` | Delete | Phase 1.2 (provider layer live) |
| Any `import elevenlabs` in `parallax-v0/` | Delete | Phase 1.2 |
| `parallax-v0/SKILL.md` | Move to `parallax-skill` repo, slim to ≤ 3K | Phase 2.1 |
| Symlink `~/.claude/skills/parallax → parallax-v0` | Repoint to `parallax-skill` | Phase 2.1 |
| `PARALLAX CONTENT/scripts/tick.py` | Migrate into `narrative-parallax` repo, delete from Drive folder | Layer 4 lifecycle test passing |
| `PARALLAX CONTENT/scripts/sheets_sync.py` | Same | Layer 4 |
| `PARALLAX CONTENT/scripts/intake_from_sheet.py` | Same | Layer 4 |
| `PARALLAX CONTENT/scripts/frameio_revisions.py` | Same | Layer 4 |
| `PARALLAX CONTENT/scripts/run_render.py` | Same (replaced by Pydantic-AI agent loop) | Layer 4 |
| `PARALLAX CONTENT/scripts/upload_to_frameio.py` | Same | Layer 4 |
| `PARALLAX CONTENT/scripts/process_docx.py` | Same | Layer 4 |
| `PARALLAX CONTENT/scripts/concept_store.py` | Same | Layer 4 |
| `PARALLAX CONTENT/scripts/drive_layer.py` | Same | Layer 4 |
| `PARALLAX CONTENT/scripts/preflight.py` | Same | Layer 4 |
| `PARALLAX CONTENT/scripts/mark_*.py` | Same | Layer 4 |
| `PARALLAX CONTENT/scripts/` | Delete entire dir | All scripts migrated; orchestrator running on staging |

**Rule:** never delete a script without first running `git grep` from inside `PARALLAX CONTENT` to confirm nothing else in the Drive folder calls it. If something does, migrate the caller too.

---

## Working rules for this refactor

- **One worktree per layer.** Branch names: `refactor-v3-cli`, `refactor-v3-skill`, `refactor-v3-narrative-skill`, `refactor-v3-orchestrator`.
- **Update `[INCOMPLETE]` → `[COMPLETE]`** at the end of every work session, with a one-line note. The marker is your bookmark.
- **PRs reference the phase number** (e.g. "Phase 1.2 — OpenRouter provider layer"). `git log --grep="Phase 1\."` becomes the second index.
- **DEV_LOG entry per merged phase.** Use `[CHANGED]` tag with `Breaks if:` line copied from the phase.
- **Fail fast on missing config.** No silent fallbacks. Missing `OPENROUTER_API_KEY` raises with the exact var name and where to set it.
- **No backwards compat shims** unless explicitly needed for a Layer 4 staged migration.

---

## Cross-references

- `~/Documents/GitHub/narrative-parallax/KIMI_PLAN.md` — authoritative for Layer 4 phases.
- `parallax-v0/VISION.md` — long-term direction; Module Architecture section still binding.
- `parallax-v0/AGENTS.md` — CLI reference. **Update on every Layer 1 phase that changes a command or flag.**
- `parallax-v0/CLAUDE.md` — session-level rules; still active.
- `~/Library/CloudStorage/.../PARALLAX CONTENT/0018-alpha-lion (ian)/notes.md` — origin of the latest decisions.
