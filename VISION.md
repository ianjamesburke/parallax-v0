# Parallax v0 — Vision

## What this repo is

A minimal agentic image-generation CLI. One agent, one tool (`generate_image`), two backends (Claude subscription via `claude-agent-sdk`, or raw Anthropic API key), five FAL model aliases, append-only session + usage logs.

The smallest possible expression of "a creative brief goes in, image files come out" without any infrastructure we haven't proven we need.

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
- Not distributed. `uv tool install` has not been live-tested.
- Not multi-agent. One flat agent; HoP / Editor / Compose is a Parallax-proper concern, not v0's.

## Deliberate deferrals (additive, no rewrite required)

- Reference-image support for `premium` (Flux Kontext, different model underneath) and `grok` (no FAL edit endpoint yet).
- Arbitrary FAL params (`strength`, `guidance_scale`, `aspect_ratio`, `num_images`). Same footgun class as letting the agent pass raw model IDs — deferred until an actual use case appears.
- Distribution: `uv tool install git+<repo>` one-liner verify + a minimal README. Needed before anyone other than me uses this.
- Parallel multi-model compare (same brief across aliases, side-by-side output). Latency optimization, not new capability.

## How v0 relates to Parallax-proper

v0 is the still-gen primitive. The manifest-first video pipeline consumes it: an Editor agent writes `.parallax/manifest.yaml`, a deterministic `parallax compose` step renders exactly what the manifest specifies, and `generate_image` is the tool the compose step calls when the manifest says "generate a still here." That means v0's tool contract (prompt + model alias + optional references → file path) is the same contract the compose step needs — nothing to rebuild when v1 arrives. What v1 adds is schema, approval gates between pipeline stages, and the HoP / Editor split on top.

## How v0 relates to Plexi

The broader Parallax is intended to wrap as a Plexi app eventually, routing LLM calls through Plexi intelligence instead of direct Anthropic. v0's two-backend dispatcher is the seam where a third backend (`plexi-intelligence`) will plug in. No shape change expected — same `run(brief, session_id)` contract.

## Out-of-scope explicitly

- No mask / inpaint / outpaint editing flows in v0.
- No audio, video, or other modalities.
- No workflow / state machine / approval gates — that lives in Parallax-proper.
- No GUI / chat front-end — v0 is CLI-only.
