# Parallax — Agent & CLI Reference

This file is the authoritative guide for agents and humans using the Parallax CLI. Keep it current whenever commands, plan YAML fields, or pipeline behavior changes.

## Workflow

### `parallax produce` — execute from a plan YAML

Reads a pre-planned scene manifest and runs the pipeline directly — no agent, no replanning. Write specific scene prompts in the YAML; produce runs them deterministically.

```sh
parallax produce \
  --folder "/path/to/project" \
  --plan  "/path/to/project/parallax/scratch/my_plan.yaml"
```

**`--folder`** — the project root. Output lands in `{folder}/parallax/output/vN/`.
**`--plan`** — a YAML file describing every scene, voice, model, and optional locked stills.

---

## Plan YAML schema

```yaml
# --- Settings ---
voice: bella              # ElevenLabs voice name (default: george)
speed: 1.1                # TTS speed multiplier (default: 1.1)
model: nano-banana        # image model alias (default: mid)
resolution: 1080x1920     # output resolution (default: 1080x1920)
caption_style: bangers    # bangers | impact | bebas | anton | clean (default: anton)
fontsize: 55              # caption font size (default: 55)
words_per_chunk: 1        # words per caption chunk (default: 1)
captions: skip            # omit this line to enable captions
headline: THE REAL REASON # omit to skip headline overlay
headline_fontsize: 64
headline_bg: white
headline_color: black

# --- Locked assets (skip regeneration) ---
character_image: parallax/scratch/ref.png   # relative to --folder
audio_path: parallax/output/v6/voiceover.mp3
words_path: parallax/output/v6/vo_words.json

# --- Scenes ---
scenes:
  - index: 0
    shot_type: character          # character | broll | screen
    reference: true               # use character_image as reference
    vo_text: "Words spoken here."
    prompt: "Image generation prompt."
    # still_path: parallax/output/v6/nano-banana_abc123.png  # lock in a generated still

  - index: 1
    shot_type: broll
    animate: true                 # generate video clip via Grok image-to-video
    clip_path: parallax/output/v17/scene_01_animated.mp4  # lock approved clip
    motion_prompt: "Slow drift..."
    zoom_direction: up            # up | down | left | right | in  (progressive zoom+pan)
    zoom_amount: 1.30             # zoom factor: 1.0 = none, 1.3 = 30% zoom in over clip
    vo_text: "More words."
    prompt: "Another image prompt."
    reference_images:             # explicit reference paths (overrides reference: true)
      - download.jpeg
```

#### Avatar block

```yaml
avatar:
  image: parallax/scratch/avatar_blue_bg.png   # blue-screen source image
  full_audio: true              # one Aurora call for full voiceover (not per-scene)
  avatar_track: parallax/output/v12/avatar_track.mp4          # lock after first gen
  avatar_track_keyed: parallax/output/v12/avatar_track_keyed.mov  # pre-keyed ProRes 4444
  track_start_s: 0.0
  position: bottom_left         # bottom_left | bottom_right | top_left | top_right
  size: 0.70                    # fraction of frame width
  y_offset_pct: 0.24            # vertical: H*(1-y_offset_pct)-h from top
```

Lock `avatar_track` and `avatar_track_keyed` immediately after first successful run — the CLI prints ready-to-paste YAML for both.

### Key rules

- **`still_path`** — if set, that still is reused and image generation is skipped for that scene. Relative paths resolve from `--folder`. Use this to lock in approved stills so only changed scenes regenerate on the next run.
- **`reference`**: `true` — passes `character_image` to the model as a reference image. Only works on models that support reference images (`mid`, `kontext`, `nano-banana`).
- **`reference_images`** — explicit list of paths (relative to `--folder`) for this scene's references. Overrides `reference: true`.
- **`audio_path` + `words_path`** — if both are set, voiceover generation is skipped and these files are reused.
- All paths (still_path, reference_images, character_image, audio_path, words_path) resolve relative to `--folder` when not absolute.

---

## Model aliases

| alias | FAL endpoint | ~price | reference images |
|---|---|---|---|
| `draft` | flux/schnell | $0.003/MP | — |
| `mid` | flux/dev | $0.025/MP | 1 |
| `premium` | flux-pro/v1.1 | $0.04/MP | — |
| `kontext` | flux-pro/kontext | $0.04/image | 1 — best character consistency |
| `nano-banana` | gemini-2.5-flash-image | $0.039/image | up to 8 |
| `grok` | xai/grok-imagine | $0.02/image | — |

All models generate 9:16 portrait (1080×1920) by default via per-model `portrait_args`.

---

## Iteration pattern

The plan YAML is the single artifact you edit between versions. Lock in stills and audio you're happy with; leave prompts unlocked for scenes you want to regenerate.

```sh
# First full run — generates everything, creates v1
parallax produce --folder ./rise1 --plan ./rise1/parallax/scratch/plan.yaml

# After approving stills: add still_path to each scene in the YAML, re-run
# Only unlocked scenes regenerate; output lands in v2
parallax produce --folder ./rise1 --plan ./rise1/parallax/scratch/plan.yaml
```

Each `produce` run calls `scan_project_folder`, which auto-increments the output version (v1 → v2 → v3…).

---

## Other commands

```sh
parallax voices --filter female     # browse ElevenLabs voices
parallax usage                      # cost + call summary
parallax update                     # upgrade via uv

# Test a single scene's video filter without running the full pipeline
parallax test-scene --folder ./project --plan ./project/parallax/scratch/plan.yaml --index 0
```

`test-scene` applies only the zoom/pan filter for the given scene index, saves to `/tmp/parallax_test_scene_NN.mp4`, and opens it. Use this to verify zoom, direction, and amount before committing to a full produce run.

---

## Environment variables

| var | purpose |
|---|---|
| `FAL_KEY` | required for real image/video generation |
| `AI_VIDEO_ELEVENLABS_KEY` / `ELEVENLABS_API_KEY` | required for voiceover generation |
| `PARALLAX_TEST_MODE=1` | Pillow shim instead of FAL — no spend |
| `PARALLAX_OUTPUT_DIR` | override default `output/` directory |
| `PARALLAX_USAGE_LOG` | override `~/.parallax/usage.ndjson` |
| `PARALLAX_LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` |
