---
name: parallax-cli
description: Operator guide for the parallax CLI — brief → plan → produce loop, plan locking, model aliases.
version: 0.3.0
---

# parallax — generic CLI skill

Parallax produces short-form vertical (or any-aspect) video from a YAML spec. Two iteration artifacts: **`brief.yaml`** is the human spec (goal, voice, script, provided assets); **`plan.yaml`** is the engine spec (per-scene model picks, locked asset paths, prompts). You iterate on both via the `parallax` CLI.

## The conversational loop

1. **Author a `brief.yaml`** in the project folder.
2. **Plan:** `parallax plan --folder <project>` materializes `<project>/parallax/scratch/plan.yaml` from the brief.
3. **Produce + iterate:** `parallax produce --folder <project> --plan <plan.yaml>`. Inspect the output mp4 in `parallax/output/vN/`. Edit the plan.yaml (lock approved assets, tweak prompts, swap model aliases) and re-run. Each run auto-increments `vN`.

Shortcuts: `--brief <brief.yaml>` on `produce` plans + produces in one shot; `--scene <N>` runs a single scene.

`plan.yaml` is the single file you edit between iterations. Never bypass it to regenerate assets ad-hoc.

## brief.yaml shape

```yaml
goal: "Promote the new product"
aspect: "9:16"             # MUST be quoted — YAML parses 9:16 as base-60 otherwise
voice: Kore                # Gemini TTS voice
voice_speed: 1.0
assets:
  provided:
    - path: brand/logo.png
      kind: product_ref
script:
  scenes:
    - index: 0
      shot_type: character     # character | broll | screen
      vo_text: "Opening line."
      prompt: "Founder in golden hour..."
    - index: 1
      shot_type: broll
      animate: true
      vo_text: "..."
      prompt: "..."
      motion_prompt: "Slow zoom on..."
```

## plan.yaml locking conventions

After approving an asset, paste its path into the corresponding scene field so the next run skips regeneration:

- **Approved still:** set `still_path:`
- **Approved voiceover:** set both `audio_path:` and `words_path:`
- **Approved animated clip:** set `clip_path:`

## Model aliases

Three tiers per modality: `draft`, `mid`, `premium`. Defaults: `mid` for image and video; `tts-mini` for TTS. Named aliases for power users: `nano-banana`, `seedream`, `gemini-3-pro` (image); `seedance`, `kling`, `veo`, `wan`, `sora` (video).

```sh
parallax models list           # browse the catalog with prices
parallax models show <alias>   # capabilities for one alias
```

## Aspect ratio

Set top-level on the brief or plan. Override per run with `--aspect`. Choices: `9:16` `16:9` `1:1` `4:3` `3:4`. Always quote in YAML.

## Response terseness rule

> **Responses are 1–3 sentences. No analysis preamble. No multi-paragraph wrap-ups. After generating an asset, show it and ask one short question.**

## Environment

- `OPENROUTER_API_KEY` — required for any real run (image, video, TTS all route through OpenRouter).
- `PARALLAX_TEST_MODE=1` — deterministic stubs, no network, no spend.

Run `parallax credits` before a real job. Run `parallax --help` for the full command tree.
