---
name: parallax
description: >
  AI video production with the parallax CLI — plan YAML iteration, stills generation,
  voiceover, Ken Burns assembly, animated clips, avatar overlay, captions, and headline.
  Use when producing short-form vertical video (TikTok, Reels, Shorts) with the parallax tool.
last_synced: 2026-04-20
---

# Parallax Video Production Skill

Parallax is a CLI-first AI video pipeline. Everything flows through a plan YAML — you edit the YAML between versions, lock approved assets, and re-run. Never bypass the plan to regenerate individual assets ad-hoc.

**Invoke via:** `uv run parallax` from the parallax-v0 repo root, or `parallax` if installed globally.

**Project root:** `/Users/ianburke/Documents/github/parallax-v0/`

---

## Pre-Production Checklist

Before writing any scene prompts:

- **Real brand in the brief?** Fetch their website first. Note visual style (dark vs. light, color palette, UI aesthetic). If you can screenshot a relevant product UI or branded image, save it locally and add it to `reference_images` for product/broll scenes — nano-banana accepts up to 8 reference images and the brand visual will anchor the scene aesthetic far better than a text description alone.
- **Scene length:** Keep every scene under 6 seconds (~18 words of VO max). Grok clips are fixed at ~6s — anything longer requires looping. Split rather than stretch.
- **Working resolution:** Default to `resolution: 480x854` for iteration. Upgrade to 1080x1920 only for final delivery.

---

## The One Command You Need

```sh
parallax produce --folder <project-dir> --plan <plan.yaml>
```

That's it. One command runs the full pipeline: stills → animation → voiceover → assembly → captions → headline → avatar overlay.

---

## Plan YAML — The Iteration Artifact

The plan YAML is the single file you edit between versions. Lock approved assets with path fields; leave prompts unlocked for scenes you want to regenerate.

```yaml
voice: bella              # ElevenLabs voice (default: george)
speed: 1.1                # TTS speed multiplier
model: nano-banana        # image model alias
caption_style: bangers    # bangers | impact | bebas | anton | clean
headline: THE BIG CLAIM   # omit to skip
# captions: skip          # uncomment to disable captions

# Lock voiceover — skips regeneration
audio_path: .parallax/output/v6/voiceover.mp3
words_path: .parallax/output/v6/vo_words.json

# Character reference image for scenes with reference: true
character_image: .parallax/scratch/ref.png

# Avatar (lip-sync PiP overlay)
avatar:
  image: .parallax/scratch/avatar_blue_bg.png   # blue bg for chroma key
  avatar_track: .parallax/output/v12/avatar_track.mp4          # lock after first gen
  avatar_track_keyed: .parallax/output/v12/avatar_track_keyed.mov  # pre-keyed ProRes 4444
  track_start_s: 0.0
  position: bottom_left   # bottom_left | bottom_right | top_left | top_right
  size: 0.70              # fraction of frame width
  y_offset_pct: 0.24      # vertical position: H*(1-y_offset_pct)-h from top
  full_audio: true        # one Aurora call for full voiceover (not per-scene)

scenes:
  - index: 0
    shot_type: broll        # broll | character | screen
    vo_text: "Words spoken here."
    prompt: "Pixar-style 3D, 9:16 vertical..."
    still_path: .parallax/output/v6/nano-banana_abc123.png  # lock approved still
    animate: true           # generate video clip via Grok i2v
    clip_path: .parallax/output/v17/scene_00_animated.mp4   # lock approved clip
    motion_prompt: "Slow camera drift, warm light..."
    zoom_direction: up      # up | down | left | right | in
    zoom_amount: 1.30       # zoom factor (1.0 = no zoom, 1.3 = 30% zoom in)

  - index: 1
    shot_type: broll
    vo_text: "More words."
    prompt: "..."
```

### Lockable fields — lock these once approved to prevent regeneration

| Field | Skips |
|---|---|
| `still_path` | Image generation for that scene |
| `animate: true` + `clip_path` | Grok i2v call for that scene |
| `audio_path` + `words_path` | Voiceover generation (both required) |
| `avatar.avatar_track` | Aurora avatar generation |
| `avatar.avatar_track_keyed` | Chroma key step |

### Model aliases

| alias | provider | reference images | notes |
|---|---|---|---|
| `nano-banana` | gemini-2.5-flash-image | up to 8 | default; best for character consistency |
| `mid` | flux/dev | 1 | good general purpose |
| `kontext` | flux-pro/kontext | 1 | best character consistency (flux) |
| `draft` | flux/schnell | — | fast/cheap drafts |
| `premium` | flux-pro/v1.1 | — | highest quality |
| `grok` | xai/grok-imagine | — | good for stylized scenes |

---

## Pipeline Steps (in order)

1. **Scan folder** → auto-increments output version (v1 → v2 → v3…)
2. **Generate stills** — skips scenes with `still_path` already set
3. **Animate scenes** — Grok i2v for scenes with `animate: true` and no `clip_path`; strips audio from clips with `-an -c:v copy` (Grok generates synthetic audio that overrides voiceover)
4. **Generate voiceover** — skips if `audio_path` + `words_path` both set
5. **Align scenes** — assigns start/end/duration to each scene from word timings
6. **Write manifest**
7. **Ken Burns assemble** — combines animated clips and Ken Burns stills into draft
8. **Burn captions** — skipped if `captions: skip`
9. **Burn headline** — skipped if no `headline`
10. **Avatar overlay** — skipped if no `avatar` block

---

## Avatar Workflow

Aurora (FAL lip-sync) generates an avatar track from the character image + full voiceover. Pre-key it once to ProRes 4444 with alpha — then composite without any chroma filter.

```yaml
avatar:
  full_audio: true          # one Aurora call for the entire voiceover
  avatar_track: .parallax/output/v12/avatar_track.mp4
  avatar_track_keyed: .parallax/output/v12/avatar_track_keyed.mov
```

- Always use `full_audio: true` — per-scene Aurora calls are wasteful
- Lock `avatar_track` and `avatar_track_keyed` immediately after first successful gen
- The `.mov` is ProRes 4444 with alpha channel — no chroma filter needed at composite time
- Chroma similarity default: 0.30 (tuned for blue-screen generated images; 0.1 is too tight)
- Chroma blend default: 0.03 (keep at or below 0.03 — higher values make the character transparent)

---

## Ken Burns / Zoom on Animated Clips

`zoom_direction` and `zoom_amount` work on both stills (Pillow Ken Burns) and pre-animated clips (FFmpeg filter).

```yaml
zoom_direction: up    # pan toward face / top of frame while zooming
zoom_amount: 1.30     # 1.0 = no zoom, 1.3 = 30% zoom in over clip duration
```

### FFmpeg progressive zoom — how it works

The filter scales to output size, then scales up progressively per-frame, then crops the output window anchored to the direction:

```
scale=1080:1920, scale=w='1080*(1+zd*t/dur)':h='1920*(1+zd*t/dur)':eval=frame, crop=1080:1920:cx:cy
```

**Critical gotchas:**
- FFmpeg `crop` filter does NOT support variable `w`/`h` with `t` — attempting `crop=w='expr':h='expr'` fails at initialization. Use the two-scale approach above.
- Use `t` (seconds) not `n/n_frames` for time-based expressions — Grok clips are 24fps but expressions based on `n` assume 30fps and under-travel.
- `zoom_direction`/`zoom_amount` must be explicitly forwarded from the plan YAML through `produce.py` into the scene dict — they are NOT automatically inherited.

---

## Staged Approval Flow (default workflow)

Never animate scenes the user hasn't approved. Follow this order to avoid burning Grok credits on rejected compositions:

**Stage 1 — Scene plan:** Draft the plan YAML with all scenes, prompts, and `vo_text`. No `animate: true` on any scene yet. Present the scene list and descriptions to the user for review.

**Stage 2 — Stills + Ken Burns cut:** Run `parallax produce` with no animated scenes. This generates stills and a full Ken Burns assembly with VO so the user can review pacing, composition, and cuts cheaply.

**Stage 3 — Approve and animate:** For scenes the user approves for animation, add `animate: true`. Lock the approved stills with `still_path`. Re-run — only the newly animated scenes incur Grok costs.

**Stage 4 — Lock approved clips:** Add `clip_path` to each approved animated scene. Any scene needing a re-prompt gets its `still_path` cleared and `animate: true` added; everything else stays locked.

```sh
# Stage 2 — stills + Ken Burns only (no animate: true in YAML yet)
parallax produce --folder ./project --plan ./project/.parallax/scratch/plan.yaml

# Stage 3 — after approval, add animate: true to approved scenes
parallax produce --folder ./project --plan ./project/.parallax/scratch/plan.yaml

# Stage 4 — lock clips, iterate on any remaining scenes
parallax produce --folder ./project --plan ./project/.parallax/scratch/plan.yaml
```

Each run auto-increments the output version. Output lands in `.parallax/output/vN/`.

---

## Verification Before Reporting Done

**Always sample frames before reporting that a video is complete.** The pipeline succeeds silently even if a filter does nothing.

```sh
# Sample frames at t=0, t=mid, t=end of a scene
ffmpeg -y -ss 0   -i output.mp4 -vframes 1 /tmp/frame_0s.jpg
ffmpeg -y -ss 2.2 -i output.mp4 -vframes 1 /tmp/frame_2s.jpg
ffmpeg -y -ss 4.4 -i output.mp4 -vframes 1 /tmp/frame_4s.jpg
# Then Read each jpg to visually confirm the effect is present
```

Never report a zoom, avatar, or caption as working without reading the frames. "It compiled" is not a verification.

---

## Parallax-Specific Gotchas

- **Pipeline lock enforcement:** Every generation function (`animate_scenes`, `generate_voiceover`, avatar gen, etc.) must check whether its output path already exists and skip if so — never rely on `produce.py`'s pre-count to prevent over-generation. If you add a new generation step, the skip-if-exists guard goes inside the function, not only in the caller.
- **Resolution-relative font sizes:** All base font sizes in the pipeline are calibrated for 1080p. `produce.py` computes `res_scale = width / 1080` and must apply it to every fontsize before passing to any burn function (`burn_captions`, `burn_titles`, `burn_headline`). When adding a new text-burn step, always pass `int(base_fontsize * res_scale)` — never a raw plan value.

## Key FFmpeg Gotchas

- **Grok clips are 24fps, not 30fps.** Any per-frame calculation must use `t` (seconds) not `n/n_frames` to be frame-rate agnostic.
- **Grok clips have generated audio.** Always strip with `-an -c:v copy` immediately after downloading; leaving audio in will override the voiceover.
- **Black first frame in concat.** Normalize all clips to the same codec/fps/resolution before concat (`libx264 yuv420p fps=30`). Never use `-c:v copy` across clips with different codecs.
- **ProRes 4444 for alpha.** When pre-keying an avatar, use `-pix_fmt yuva444p10le -c:v prores_ks -profile:v 4444` to preserve the alpha channel.

---

## Environment Variables

| var | purpose |
|---|---|
| `FAL_KEY` | Required for image gen, Grok i2v, Aurora avatar |
| `ELEVENLABS_API_KEY` | Required for voiceover |
| `ANTHROPIC_API_KEY` | Required for agent mode (`parallax run`) |
| `PARALLAX_TEST_MODE=1` | Pillow shim instead of FAL — zero spend |
| `PARALLAX_LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` |
