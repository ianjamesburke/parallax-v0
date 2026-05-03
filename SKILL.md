---
name: parallax
description: Operator guide for the parallax CLI — brief → plan → produce loop, plan locking, model aliases, and standalone commands.
version: 0.4.0
---

# parallax

Parallax produces short-form video from a YAML spec. Two iteration artifacts: **`brief.yaml`** is the human spec (goal, voice, aspect ratio, scene scripts); **`plan.yaml`** is the engine spec (per-scene model picks, locked asset paths, prompts). Iterate on both via the `parallax` CLI.

## Install

```sh
curl -LsSf https://raw.githubusercontent.com/ianjamesburke/parallax-v0/main/install.sh | sh
```

Or from a local checkout: `uv tool install --python 3.11 --from /path/to/parallax-v0 parallax`

```sh
parallax --version
parallax update      # pulls latest from the original install source
```

## The loop

1. Author `brief.yaml` in the project folder.
2. `parallax plan --folder <project>` → writes `<project>/parallax/scratch/plan.yaml`.
3. `parallax produce --folder <project> --plan <plan.yaml>` → writes `parallax/output/vN/`. Inspect, edit plan.yaml (lock approved assets, tweak prompts), re-run. Auto-increments `vN`.

One-shot: `parallax produce --folder <project> --brief brief.yaml` (plans + produces). Single scene: add `--scene <N>`.

`plan.yaml` is the only file you edit between iterations. Never regenerate assets ad-hoc by bypassing it.

## brief.yaml shape

```yaml
goal: "30-second product launch"
aspect: "9:16"          # MUST be quoted — YAML parses 9:16 as base-60 otherwise
voice: nova             # TTS voice (OpenAI: nova, shimmer, alloy, echo, fable, onyx)
voice_speed: 1.0
assets:
  provided:
    - path: brand/logo.png
      kind: product_ref
script:
  scenes:
    - index: 0
      shot_type: character   # character | broll | screen
      vo_text: "Opening line."
      prompt: "Founder in golden hour..."
    - index: 1
      shot_type: broll
      animate: true
      vo_text: "..."
      prompt: "..."
      motion_prompt: "Slow zoom on..."
```

## plan.yaml locking

After approving an asset, set its path so the next run skips regeneration:

- Approved still → `still_path:`
- Approved voiceover → `audio_path:` + `words_path:`
- Approved animated clip → `clip_path:`

Per-scene `aspect:` overrides the top-level value when one scene needs a different shape.

## Model aliases

Three tiers per modality: `draft`, `mid`, `premium`. Defaults: `mid` for image/video, `tts-mini` for TTS.

```sh
parallax models list                   # full catalog with prices
parallax models show <alias>           # capabilities for one alias
parallax models show tts-mini          # TTS voice list
```

Named aliases: `nano-banana`, `seedream`, `gemini-3-pro` (image); `seedance`, `kling`, `veo`, `wan`, `sora` (video).

## Video generation resolution (animate_resolution)

**Default behaviour:** Parallax generates video clips at **480p** and upscales them to the output `resolution:` during ffmpeg assembly. This is intentional — the quality difference is imperceptible on phones and the cost savings are significant.

Seedance 2.0 Fast (the `draft` alias) pricing by generation resolution:

| Resolution | $/second | 5s clip |
|------------|----------|---------|
| 480p (default) | $0.054 | ~$0.27 |
| 720p | $0.121 | ~$0.60 |
| 1080p | $0.272 | ~$1.36 |

Override in `plan.yaml` or `brief.yaml`:
```yaml
animate_resolution: 720x1280   # generate at 720p instead of 480p
```

Per-scene override (one clip at higher quality):
```yaml
scenes:
  - index: 0
    animate_resolution: 720x1280   # this scene only
```

Aspect-aware defaults: `9:16 → 480x854`, `16:9 → 854x480`, `1:1 → 480x480`.

## TTS voices

Default voice: `nova`. Valid OpenAI voices for `tts-mini`:
`alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`, `coral`, `verse`, `ballad`, `ash`, `sage`, `marin`, `cedar`

Do not use Gemini voice names (e.g. Kore, Puck) — the TTS backend is OpenAI gpt-audio-mini.

## Standalone commands

```sh
# Footage indexing
parallax ingest ./clips/               # writes clips/index.json with per-clip word timestamps
parallax ingest video.mov --estimate   # dry-run cost report

# Image
parallax image generate "prompt" --aspect 9:16 --model draft --out ./stills/
parallax image generate "prompt" --ref ./refs/face.png
parallax image analyze ./stills/frame.png ["optional question"]

# Audio
parallax audio transcribe <file>
parallax audio detect-silences <file>
parallax audio trim <file>
parallax audio cap-pauses <file>
parallax audio speed <file>

# Video
parallax video frame <file>
parallax video color <file>

# Accounting / logs
parallax credits
parallax usage
parallax log latest
parallax log list
parallax log <run-id>
```

## Environment

- `OPENROUTER_API_KEY` — required for all real runs (image, video, TTS all route through OpenRouter).
- `PARALLAX_TEST_MODE=1` — deterministic stubs, no network, no spend. Use this for planning and CI.

Run `parallax credits` before any real job.

## Shell completion

```sh
parallax completions install   # writes cache file, prints the source line to add to ~/.zshrc
```

## Response terseness rule

> **Responses are 1–3 sentences. No analysis preamble. After generating an asset, show it and ask one short question.**
