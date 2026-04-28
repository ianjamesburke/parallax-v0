# Parallax v0 — Vision

## What this repo is

A minimal agentic image-generation CLI. One agent, one tool (`generate_image`), two backends (Claude subscription via `claude-agent-sdk`, or raw Anthropic API key), five FAL model aliases, append-only session + usage logs.

The smallest possible expression of "a creative brief goes in, image files come out" without any infrastructure we haven't proven we need.

## Product principles

- **Installable on any Mac, one command.** `uv tool install --python 3.11 git+<repo>` must be the entire install story — no manual venv, no Python-version hunting, no brew dance. If someone else can't get to a working `parallax run` from a clean Mac in under five minutes, that's a bug.
- **Updatable the same way.** `uv tool upgrade parallax` on any machine pulls the latest release. Install and update are the same shape so the instructions fit on a sticky note.

## Why v0 exists as its own thing

The broader Parallax is a video production pipeline with three agent roles (HoP / Editor / Compose) and a manifest as the contract. That's a lot of architecture to write at once, and most of the load-bearing questions — model ladder, cost accounting, session resume, backend abstraction, reference-image handling — live at the primitive layer. v0 is the primitive, stress-tested in isolation, so the video pipeline on top of it can be built without simultaneously designing the primitive.

## Current state (2026-04-18)

- Two backends behind a dispatcher; default = Claude subscription.
- Model ladder: `draft` / `mid` / `premium` / `nano-banana` / `grok`. Agent sees aliases only; pricing table maps to FAL IDs.
- Real FAL integration, prompt-only + reference-image support on `mid` (flux/dev img2img, 1 ref) and `nano-banana` (gemini edit, 8 refs).
- Per-call NDJSON usage log; `parallax usage` aggregates by alias and session.
- 50 hermetic tests + opt-in live FAL test gated on `PARALLAX_LIVE_FAL=1`.

## What v0 is NOT

- Not the video pipeline. No storyboard, no timeline, no assembly, no captions.
- Not manifest-driven. The agent calls tools directly; there is no `.parallax/manifest.yaml` yet.
- Not multi-agent. One flat agent; HoP / Editor / Compose is a Parallax-proper concern, not v0's.

## Deliberate deferrals (additive, no rewrite required)

- Reference-image support for `premium` (Flux Kontext, different model underneath) and `grok` (no FAL edit endpoint yet).
- Arbitrary FAL params (`strength`, `guidance_scale`, `aspect_ratio`, `num_images`). Same footgun class as letting the agent pass raw model IDs — deferred until an actual use case appears.
- Parallel multi-model compare (same brief across aliases, side-by-side output). Latency optimization, not new capability.
- Auto-update / update-check nag in the CLI. `uv tool upgrade` is the contract for now; revisit if manual upgrades become a drag on clients.

## How v0 relates to Parallax-proper

v0 is the still-gen primitive. The manifest-first video pipeline consumes it: an Editor agent writes `.parallax/manifest.yaml`, a deterministic `parallax compose` step renders exactly what the manifest specifies, and `generate_image` is the tool the compose step calls when the manifest says "generate a still here." That means v0's tool contract (prompt + model alias + optional references → file path) is the same contract the compose step needs — nothing to rebuild when v1 arrives. What v1 adds is schema, approval gates between pipeline stages, and the HoP / Editor split on top.

## How v0 relates to Plexi

The broader Parallax is intended to wrap as a Plexi app eventually, routing LLM calls through Plexi intelligence instead of direct Anthropic. v0's two-backend dispatcher is the seam where a third backend (`plexi-intelligence`) will plug in. No shape change expected — same `run(brief, session_id)` contract.

## Out-of-scope explicitly

- No mask / inpaint / outpaint editing flows in v0.
- No workflow / state machine / approval gates — that lives in Parallax-proper.
- No GUI / chat front-end — v0 is CLI-only.

---

## Module Architecture — Target State

`tools_video.py` is a 70KB monolith that accumulated everything during rapid development. The target is clean domain modules with a thin public surface, each of which maps 1:1 to a CLI subcommand group:

| Module | Owns | CLI group |
|---|---|---|
| `parallax/audio.py` | TTS generation, Whisper transcription, tempo adjustment, pause trimming | `parallax audio` |
| `parallax/video.py` | Frame extraction, color sampling, Ken Burns, clip assembly, compositing, avatar overlay | `parallax video` |
| `parallax/stills.py` | Image generation (FAL), still locking, reference image handling | `parallax stills` |

`tools_video.py` becomes an implementation detail — a private module that the domain modules import from. It is not deleted in one go; it dissolves as functions are touched for real reasons (bug fixes, new features) and migrated into the right domain module.

### Migration strategy

**Phase 1 (now):** Create `parallax/audio.py` and `parallax/video.py` as thin public surfaces. They import from `tools_video.py` and re-export a clean API. No behavior changes. This gives agents and callers the correct namespace immediately.

**Phase 2 (ongoing):** Each time a function in `tools_video.py` is touched for a real reason, move it into the appropriate domain module and update callers. Never migrate a function speculatively — only when you're already in there. The monolith dissolves function-by-function with zero regression risk.

**Priority deferred extraction — the narration pipeline:**
The TTS/voiceover stack (`generate_voiceover`, `_apply_atempo`, `_trim_long_pauses`, fixed-WPM fallback) is the largest self-contained chunk in `tools_video.py` and the clearest candidate for `parallax/audio.py`. It has no video dependencies. Extract it as a unit when next touching audio behavior.

### Agent-facing benefit

Agents naturally reach for `from parallax.audio import transcribe_words` or `parallax video frame ...`. Making the namespace match that instinct eliminates the discovery loop agents currently run when the function doesn't exist where they expect it.
