---
name: parallax-cli
description: Operator + agent guide for the parallax CLI — short-form vertical video production via plan YAML. Read this before invoking parallax to understand which subcommand to reach for, how to write/iterate plans, and which gotchas bite.
version: 0.2.0
---

# parallax CLI — operator and agent guide

The parallax CLI produces short-form 9:16 video ads from a plan YAML. You write a plan, run `parallax produce`, get back an mp4. Iterate by editing the plan and re-running. Stills/audio/clips you approve can be locked in the plan so subsequent runs only regenerate what changed.

This document is the single source of truth for **how to use the CLI to get a job done**. The full reference (every flag, every model id, every YAML field) lives in `AGENTS.md`. Read this for *workflows*; cross-reference AGENTS.md when you need exact field names.

## Installation

```sh
# install / upgrade
uv tool install git+https://github.com/ianjamesburke/parallax-v0
parallax update         # upgrade in place after first install
parallax --help         # confirm installed
```

Required env:

| var | when |
|---|---|
| `OPENROUTER_API_KEY` | every real-mode run (image / video / TTS) |
| `PARALLAX_TEST_MODE=1` | for dry runs — no network, no spend, deterministic stubs |
| `PARALLAX_LOG_LEVEL` | optional — `INFO` / `DEBUG` to override default WARNING |

All TTS routes through OpenRouter (Gemini 2.5 Flash Preview TTS) — see `parallax models show gemini-flash-tts` for the voice list.

Always check credits before kicking off a real run:

```sh
parallax credits
```

## Commands

### `produce` — run a plan end-to-end

The main command. Reads a plan YAML, executes every step (stills → voiceover → assembly → captions → headline), writes the final mp4 to `{folder}/parallax/output/vN/`.

```sh
parallax produce --folder /path/to/project --plan /path/to/project/parallax/scratch/plan.yaml
```

`--folder` is the project root. All relative paths inside the plan resolve from this folder. Each run auto-increments the output version (`v1` → `v2` → …), so you never overwrite previous output.

### `test-scene` — preview one scene's video filter

Apply only the zoom/pan filter for a single scene, save to `/tmp/parallax_test_scene_NN.mp4`, and open it. Use this to verify zoom direction and amount before running the full pipeline.

```sh
parallax test-scene --folder ./project --plan ./project/parallax/scratch/plan.yaml --index 2
```

### `usage` — cost summary

Per-model and per-session usage from `~/.parallax/usage.ndjson`.

```sh
parallax usage
parallax usage --include-test    # include PARALLAX_TEST_MODE records (off by default)
```

### `tail` — stream the run log

Every `produce` run gets a `run_id` and writes a JSONL event log to `~/.parallax/logs/<run_id>.log`.

```sh
parallax tail latest          # most recent run
parallax tail latest -f       # follow new events live
parallax tail <run_id>        # specific run
```

### `audio *` — audio utilities

- `parallax audio transcribe <file> --out words.json` — produce word-level timestamps JSON. Used when you have an existing voiceover and want to lock it into a plan via `audio_path` + `words_path`.
- `parallax audio detect-silences <file>` — list silent sections. Pipe the timestamps into `audio trim`.
- `parallax audio trim --plan <plan> --folder <folder> --start <s> --end <s>` — remove a time range from voiceover, avatar track, and words; updates `plan.yaml` in place.
- `parallax audio cap-pauses --input <wav> --output <wav> --max-gap 0.75` — cap inter-word gaps without amplitude probing (uses WhisperX word boundaries).

### `video *` — video utilities

- `parallax video frame <file> <time>` — extract one frame at `time` seconds.
- `parallax video color <file> --x 10 --y 10 --time 2.0` — sample a pixel; prints `0xRRGGBB`. Useful for finding the exact background color of an avatar source for chroma-key.

### `verify-suite` — run an `expected.yaml` test suite

Walks every case folder under a suite directory, runs `produce` on each, and asserts every field of `expected.yaml`. Designed for `PARALLAX_TEST_MODE=1` so it's free.

```sh
PARALLAX_TEST_MODE=1 parallax verify-suite tests/fixtures/verify_suite_smoke/
parallax verify-suite my_suite/ --case basic     # single case
parallax verify-suite my_suite/ --paid           # opt in to paid: true cases
```

Use `parallax verify-init <target>` to scaffold a new case folder with a starter `plan.yaml` + `expected.yaml`.

### `credits` — OpenRouter balance

Returns non-zero if remaining < $0.50. Run before kicking off real-mode work.

```sh
parallax credits
```

## Plan YAML schema

Minimum viable plan:

```yaml
stills_only: true             # omit (or set false) to proceed through voiceover + video
voice: kore                   # Gemini TTS voice — see full list in schema below
model: nano-banana
scenes:
  - index: 0
    shot_type: character
    reference: true           # always set reference: true when character_image is present
    vo_text: "Words spoken in scene zero."
    prompt: "A medium close-up of a tired barista at a sunlit counter."
  - index: 1
    shot_type: broll
    reference: true           # set reference: true on broll scenes too when the character should appear
    vo_text: "Words spoken in scene one."
    prompt: "Steam rising from a fresh espresso shot, shallow depth of field."
```

Full schema (every block optional unless marked **required**):

```yaml
# --- Settings ---
stills_only: true             # stop after stills generation — skip voiceover, video, assembly (use for stills-mode output)
voice: kore                   # Gemini TTS voice name (default: kore). Valid names:
                              #   achernar, achird, algenib, algieba, alnilam, aoede, autonoe,
                              #   callirrhoe, charon, despina, enceladus, erinome, fenrir, gacrux,
                              #   iapetus, kore, laomedeia, leda, orus, puck, pulcherrima,
                              #   rasalgethi, sadachbia, sadaltager, schedar, sulafat, umbriel,
                              #   vindemiatrix, zephyr, zubenelgenubi
                              # Run `parallax models show gemini-flash-tts` for the canonical list.
speed: 1.1                    # TTS speed multiplier (default: 1.1)
model: nano-banana            # image alias — see "Image" table in AGENTS.md (default: mid)
resolution: 720x1280          # WxH; if omitted, inherits from clip_path probe or defaults to 720x1280
hq: true                      # request 720p clips from image-to-video model (default: 480p)
caption_style: bangers        # bangers | impact | bebas | anton | clean (default: anton)
fontsize: 55                  # caption font size
words_per_chunk: 1            # words per caption chunk
captions: skip                # omit to enable captions
headline: THE REAL REASON     # omit to skip headline overlay
headline_fontsize: 64
headline_bg: white
headline_color: black

# --- Locked assets (skip regeneration) ---
character_image: parallax/scratch/ref.png      # relative to --folder
audio_path: parallax/output/v6/voiceover.mp3   # lock voiceover (must pair with words_path)
words_path: parallax/output/v6/vo_words.json   # WhisperX word timings

# --- Avatar block (PiP overlay) ---
avatar:
  image: parallax/scratch/avatar_blue_bg.png
  full_audio: true
  avatar_track: parallax/output/v12/avatar_track.mp4
  avatar_track_keyed: parallax/output/v12/avatar_track_keyed.mov
  position: bottom_left       # bottom_left | bottom_right | top_left | top_right
  size: 0.70                  # fraction of frame width
  y_offset_pct: 0.24

# --- Scenes (required, must have at least one) ---
scenes:
  - index: 0                          # required, monotonic from 0
    shot_type: character              # character | broll | screen — required
    reference: true                   # use character_image as reference (image models only)
    vo_text: "Spoken text."           # required for full pipeline
    prompt: "Image generation prompt."  # required unless still_path is set
    still_path: parallax/output/v6/scene_00.png   # lock approved still — skips image gen
    duration_s: 4.5                   # override derived VO duration
    start_offset_s: 0.5
    fade_in_s: 0.2
    fade_out_s: 0.3

  - index: 1
    shot_type: broll
    animate: true                     # generate video clip (Grok image-to-video)
    motion_prompt: "Slow drift toward the espresso machine."
    zoom_direction: up                # up | down | left | right | in
    zoom_amount: 1.30                 # zoom factor over the clip
    clip_path: parallax/output/v17/scene_01_animated.mp4   # lock approved clip
    vo_text: "Words for scene one."
    prompt: "Image prompt for the still that gets animated."
    reference_images:                 # explicit override of `reference: true`
      - download.jpeg
```

### Path resolution rules

- All non-absolute paths (`character_image`, `still_path`, `clip_path`, `audio_path`, `words_path`, `reference_images`, `avatar.*`) resolve relative to `--folder`.
- If both `audio_path` and `words_path` are set, voiceover generation is skipped entirely.
- If `still_path` is set on a scene, that still is reused and image gen is skipped for that scene only.
- If `clip_path` is set on an `animate: true` scene, that clip is reused and video gen is skipped.

### Output layout

```
{folder}/
├── parallax/
│   ├── scratch/
│   │   └── plan.yaml
│   └── output/
│       ├── v1/
│       │   ├── stills/
│       │   ├── audio/
│       │   ├── manifest.yaml
│       │   └── {project}-v1.mp4
│       └── v2/...
```

## Common workflows

### 1. Basic produce — fresh project

```sh
# Set up the project folder, write a plan, run.
mkdir -p ./mybrand/parallax/scratch
# (write ./mybrand/parallax/scratch/plan.yaml — see Plan YAML schema above)
parallax credits   # confirm balance
parallax produce --folder ./mybrand --plan ./mybrand/parallax/scratch/plan.yaml
parallax tail latest    # watch progress / debug
```

Final mp4 lands at `./mybrand/parallax/output/v1/mybrand-v1.mp4`.

### 2. Iterate — lock approved assets, regenerate one scene

After v1, you like scenes 0 and 2 but want to regenerate scene 1. Edit the plan:

```yaml
scenes:
  - index: 0
    still_path: parallax/output/v1/stills/scene_00.png    # locked
    vo_text: "..."
    prompt: "..."
  - index: 1
    # no still_path → will regenerate
    vo_text: "Updated copy here."
    prompt: "Updated visual concept."
  - index: 2
    still_path: parallax/output/v1/stills/scene_02.png    # locked
    vo_text: "..."
    prompt: "..."

# Lock voiceover too if it's still good
audio_path: parallax/output/v1/audio/voiceover.mp3
words_path: parallax/output/v1/audio/vo_words.json
```

Re-run the same command. Output goes to `v2/`. Only scene 1 regenerates; everything else is reused.

### 3. Single-scene preview — verify zoom before full produce

When you've added `zoom_direction: in` + `zoom_amount: 1.4` to a scene and want to see if it looks right without paying for a full run:

```sh
parallax test-scene --folder ./mybrand --plan ./mybrand/parallax/scratch/plan.yaml --index 1
```

Opens the resulting clip at `/tmp/parallax_test_scene_01.mp4`. Adjust `zoom_amount` and re-run as needed.

### 4. Test mode vs paid mode

Test mode never hits the network and is free. Use it for CI, smoke tests, agent simulation, schema validation:

```sh
PARALLAX_TEST_MODE=1 parallax produce --folder ./mybrand --plan ./mybrand/parallax/scratch/plan.yaml
PARALLAX_TEST_MODE=1 parallax verify-suite tests/fixtures/verify_suite_smoke/
```

Real mode requires `OPENROUTER_API_KEY` and credits. The CLI will refuse with `InsufficientCreditsError` if balance is too low.

### 5. Avatar overlay workflow

First run generates `avatar_track.mp4` and `avatar_track_keyed.mov` and prints YAML you paste back into the plan to lock them in. Subsequent runs reuse the keyed track and only re-composite.

```sh
# v1 — first run, generates the avatar track
parallax produce --folder ./mybrand --plan ./mybrand/parallax/scratch/plan.yaml
# (paste the printed avatar_track / avatar_track_keyed paths into plan.yaml)
# v2 — reuses the keyed track, just re-composites
parallax produce --folder ./mybrand --plan ./mybrand/parallax/scratch/plan.yaml
```

## Gotchas

- **Test mode is opt-in via env var.** `PARALLAX_TEST_MODE=1` must be set before invoking parallax. The CLI will not infer it from missing API keys — missing `OPENROUTER_API_KEY` in real mode produces a hard error.
- **Credits gate.** Real-mode `produce` calls `check_credits()` early. If you're below $0.50, you'll get `InsufficientCreditsError` and the run aborts before any spend. Fix with `parallax credits` to confirm, then top up.
- **Aspect-ratio reliability.** Image models default to 9:16 portrait, but Gemini honors aspect on a best-effort basis — `gemini-3-flash` is a preview model and behavior shifts day-to-day, especially when the brief contains cinematic / landscape-leaning language. **For production runs, set `model: gemini-3-pro`** — the Pro variant is meaningfully more reliable on aspect adherence than the Flash variant. `gemini-3-flash` is fine for early iteration; switch to `gemini-3-pro` once the brief is locked. If both fail aspect, the CLI's validator catches it and surfaces `AspectMismatchError` (the run blocks with a clear diagnostic) rather than silently center-cropping, which would discard subject content.
- **All paths in the plan are relative to `--folder`.** If you `cd` and run from a different directory, the plan still works as long as `--folder` is correct. Don't write absolute paths in the plan unless you mean it.
- **`resolution` cascades.** Set it once at the top of the plan; every downstream stage (stills, captions, headline, manifest) inherits. Mismatched per-scene resolutions cause stretching.
- **Avatar tracks are expensive to regenerate.** After the first successful run, immediately copy the `avatar_track` + `avatar_track_keyed` paths from stdout into the plan. Without locking, every subsequent run regenerates them from scratch.
- **`still_path` vs `clip_path`.** `still_path` locks an image; `clip_path` locks a video. They're mutually exclusive per scene — `clip_path` only applies when `animate: true`.
- **Scene 0 sets the tone.** First scene determines the captions baseline frame and headline placement; iterate on it first before downstream scenes.
- **Run logs live in `~/.parallax/logs/`.** When something breaks mid-pipeline, `parallax tail latest` is the first move. The JSONL stream shows external API calls, scene timings, and any stage that errored.
