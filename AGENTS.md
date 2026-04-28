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
resolution: 720x1280      # output resolution (optional — see "Project resolution" below)
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
- **`reference`**: `true` — passes `character_image` to the model as a reference image. Only works on models that support reference images (`draft`, `mid`, `nano-banana`, `seedream`, `premium`).
- **Timing overrides** (per scene, all optional, null/absent = derive from VO): `duration_s`, `start_offset_s`, `fade_in_s`, `fade_out_s`. When set on one scene, subsequent scenes cascade by the same delta so the timeline stays gap-free.
- **`reference_images`** — explicit list of paths (relative to `--folder`) for this scene's references. Overrides `reference: true`.
- **`audio_path` + `words_path`** — if both are set, voiceover generation is skipped and these files are reused.
- All paths (still_path, reference_images, character_image, audio_path, words_path) resolve relative to `--folder` when not absolute.

---

## Model aliases

All real-mode media generation routes through OpenRouter. ElevenLabs is retained only as the brand-locked-voice escape hatch (`voice: eleven:<voice_id>`). Aliases are unique across kinds.

### Image (`model:` field)

| alias | model_id | ~price | refs | fallback |
|---|---|---|---|---|
| `draft` | google/gemini-2.5-flash-image | $0.005/image | 4 | — |
| `mid` | bytedance/seedream-4.5 | $0.025/image | 4 | draft |
| `nano-banana` | google/gemini-2.5-flash-image | $0.039/image | 8 | seedream |
| `seedream` | bytedance/seedream-4.5 | $0.025/image | 4 | nano-banana |
| `premium` | google/nano-banana-pro | $0.080/image | 8 | nano-banana |
| `gemini-3-flash` | google/gemini-3.1-flash-image-preview | $0.039/image | 8 | nano-banana |
| `gemini-3-pro` | google/gemini-3-pro-image-preview | $0.080/image | 8 | gemini-3-flash |

### Video (used when scenes set `animate: true`)

| alias | model_id | ~price | fallback |
|---|---|---|---|
| `kling` | kuaishou/kling-video-o1 | $0.10/s | seedance |
| `veo` | google/veo-3.1 | $0.50/s | kling |
| `seedance` | bytedance/seedance | $0.06/s | wan |
| `wan` | alibaba/wan-2.5 | $0.05/s | seedance |
| `sora` | openai/sora-2-pro | $0.40/s | kling |

### TTS (default `gemini-flash-tts`)

| alias | model_id | ~price | fallback |
|---|---|---|---|
| `gemini-flash-tts` | google/gemini-3.1-flash-tts | $0.30/1k chars | gpt-4o-mini-tts |
| `gpt-4o-mini-tts` | openai/gpt-4o-mini-tts | $0.60/1k chars | voxtral |
| `voxtral` | mistral/voxtral-mini-tts | $0.20/1k chars | — |

`voice:` accepts a free-text character description (Gemini Flash TTS interprets it as a style prompt) or `eleven:<voice_id>` for direct ElevenLabs (brand-locked voices). All image and video models render 9:16 portrait by default.

---

## Project resolution

Plan-level `resolution: WxH` is the single source of truth for output dimensions. All downstream stages (Ken Burns assemble, captions, headline, titles, manifest) inherit from it.

If you omit `resolution`, parallax probes every scene's `clip_path` (when present) and picks the largest `width×height` it finds — the project inherits the natural resolution of its source video. When no probeable clips exist, the default is **`720x1280`** (vertical 9:16).

Why 720×1280 as the default? Animated clips from image-to-video models default to 480p (~480×848). Without explicit override, upscaling 480p sources to 1080p produced soft, brutal-looking output and risked aspect-ratio stretching. 720p is a balance: it's the resolution `hq: true` requests, it costs nothing extra to inherit, and it keeps the frame size matched to the actual source pixels.

Set `hq: true` to request 720p clips from the image-to-video model (default is 480p). Combined with `resolution: 720x1280` (or omitted), this gives a no-upscale pipeline: source 720×1280 → output 720×1280, no soft scaling.

The clip-mode zoom filter uses `force_original_aspect_ratio=increase` + center-crop to fit-fill the target frame, so source clips that aren't *exactly* 9:16 are no longer stretched into the target.

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

### `parallax verify-suite` — assert produce output against an `expected.yaml`

Runs every case subfolder of a suite (each containing `plan.yaml` + `expected.yaml`), executes `produce` on a temp copy, then asserts every present field of `expected.yaml`. Prints `[PASS] <name>` / `[FAIL] <name> — <field>: …` and returns non-zero on any failure. Designed to run in `PARALLAX_TEST_MODE=1` so the suite is free.

The `<suite_dir>` argument can also be a single case folder directly — if it contains both `plan.yaml` + `expected.yaml`, that case alone is run. Lets operators iterate on one case without wrapping it in a parent dir.

```sh
PARALLAX_TEST_MODE=1 parallax verify-suite tests/fixtures/verify_suite_smoke/
PARALLAX_TEST_MODE=1 parallax verify-suite tests/integration/res-720x1280/   # single case
parallax verify-suite my_suite/ --case basic           # run a single case
parallax verify-suite my_suite/ --paid                 # opt in to paid: true cases
```

The canonical reference case is `tests/integration/res-720x1280/` — copy it (or use `verify-init --from`) when authoring new cases. Its `README.md` documents every assertion block with a worked example.

### `parallax verify-init` — scaffold a new verify-suite case

Creates a new case folder at `<target>` containing `plan.yaml`, `expected.yaml`, and `README.md`. With `--from <existing>`, copies that case verbatim; with `--resolution WxH` it also rewrites the `resolution:` in `plan.yaml`, `final.resolution` and every `stages.<name>.resolution` in `expected.yaml`, and the `name:` field. Refuses to overwrite an existing target unless `--force`.

```sh
parallax verify-init tests/integration/res-480x854/ \
  --from tests/integration/res-720x1280/ \
  --resolution 480x854
parallax verify-init my-new-case/                      # minimal one-scene starter
parallax verify-init my-new-case/ --from <existing> --force
```

Schema (every block optional):

```yaml
name: my-case
paid: false
cost_usd_max: 0.0                  # run cost.usd <= this; catches API leaks

final:
  resolution: 1080x1920            # exact w×h on the final mp4 via ffprobe
  duration_s: { min: 5.0, max: 12.0 }
  audio_video_diff_s_max: 0.05
  scene_count: 4                   # length of manifest.scenes

stages:
  stills:
    files_must_exist: ["stills/*.png"]   # globs resolved under out_dir
    resolution: 1080x1920
  voiceover:
    files_must_exist: ["audio/voiceover.*", "audio/vo_words.json"]
  assemble:
    files_must_exist: ["*.mp4"]    # finalize renames the draft to <folder>-vN.mp4
    resolution: 1080x1920
    contiguous_cover: true         # manifest scenes start at 0, no gaps, cover total

manifest:
  keys_required: [model, voice, resolution, scenes]
  scene_keys_required: [index, vo_text, prompt, start_s, end_s, duration_s]

run_log:                           # ~/.parallax/logs/<run_id>.log JSONL
  must_not_contain: ["Traceback"]
  must_contain: ["plan.loaded", "run.end"]
```

Per-stage `_log` lines come through `Settings.events` (stdout) and are NOT in the runlog JSONL — only `run.start`, `plan.loaded`, external-call records, and `run.end` are. Target those when writing `run_log` assertions.

---

## Environment variables

| var | purpose |
|---|---|
| `OPENROUTER_API_KEY` | required for real image / video / tts generation |
| `AI_VIDEO_ELEVENLABS_KEY` / `ELEVENLABS_API_KEY` | required when using `voice: eleven:<id>` escape hatch |
| `PARALLAX_TEST_MODE=1` | Pillow + ffmpeg stubs — no network, no spend |
| `PARALLAX_OUTPUT_DIR` | override default `output/` directory |
| `PARALLAX_USAGE_LOG` | override `~/.parallax/usage.ndjson` |
| `PARALLAX_LOG_DIR` | override `~/.parallax/logs/` (per-run JSONL files) |
| `PARALLAX_LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` (stderr verbosity) |

## Run logs

Every `parallax produce` invocation gets a unique `run_id` and writes a JSONL event log to `~/.parallax/logs/<run_id>.log` (one event per line). Tail it with:

```sh
parallax tail <run_id>      # whole log
parallax tail latest        # most recent run
parallax tail latest -f     # follow new events
```

---

## Module map

The video pipeline used to live in a single `tools_video.py` monolith.
Block 4 split it into focused modules. `tools_video.py` is now a pure
compat shim (~70 lines of re-exports) for older imports — new code
should import from the extracted module directly.

| module | purpose |
|---|---|
| `parallax.captions` (subpackage) | Caption rendering. `styles` (presets + `resolve_caption_style`), `chunker` (`_smart_chunk_words`), `animation` (`_expand_animation_keyframes`), `drawtext` (ffmpeg backend), `pillow` (fallback backend), `burn` (`burn_captions` orchestration). |
| `parallax.assembly` | Scene timing + video assembly. `align_scenes`, `ken_burns_assemble`, `assemble_clip_video`, `_zoom_filter`, `_make_kb_clip`, `_make_clip_segment`. |
| `parallax.avatar` | `generate_avatar_clips`, `key_avatar_track`, `burn_avatar` — Aurora generation + chroma-key + PiP composite. |
| `parallax.headline` | `burn_titles`, `burn_headline` — drawtext overlays. |
| `parallax.voiceover` | `generate_voiceover` + `_apply_atempo` + `_trim_long_pauses` + `_mock_voiceover`. |
| `parallax.project` | `scan_project_folder`, `animate_scenes`. |
| `parallax.manifest` | `write_manifest`, `read_manifest`. |
| `parallax.ffmpeg_utils` | `_get_ffmpeg`, `_ffmpeg_has_drawtext`, `_probe_fps`, `_parse_color`, `_FFMPEG_FULL`. |
| `parallax.audio`, `parallax.video` | Earlier extractions (Block 3). Audio/video CLI subcommands. |
| `parallax.openrouter` | LLM/image/TTS/video calls — the only module that talks to OpenRouter. |
| `parallax.elevenlabs` | Voice resolution + ElevenLabs TTS escape hatch. |
| `parallax.gemini_tts` | Gemini Flash TTS direct path. |
| `parallax.produce` | CLI entry point — wires every step of the pipeline. |
| `parallax.tools_video` | Compat shim only. Do not add code here. |
