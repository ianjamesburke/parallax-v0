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

---

## Concept Routing (when invoked with just a number)

If the skill is invoked with only a concept ID (e.g. `/parallax 0006` or `/parallax 6`), read `concepts.json` from the project root, find the concept, and route based on status:

| Status | `frameio_link` | Action |
|---|---|---|
| `ready_for_edits` | exists | **Revision flow** — pull Frame.io comments, apply to plan, re-render, upload |
| `ready_for_edits` | null | **Initial render flow** — run pipeline, upload, flip to `ready_for_review` |
| `rendering` | any | **Upload flow** — find latest render, run `upload_to_frameio.py --id N` |
| anything else | — | Report current status and ask what to do |

No need for the user to spell out the action — infer it from the concept's state.

**Flip to `rendering` immediately on pickup.** As soon as routing resolves to an action (initial render or revision), before any pipeline work begins:
1. Write `status: rendering` to `concepts.json`
2. Push to the sheet: `uv run --with google-api-python-client --with google-auth scripts/sheets_sync.py --push-only`

This prevents the tick loop or a second agent from picking up the same concept while work is in progress.

---

**Invoke via:** `uv run parallax` from the parallax-v0 repo root, or `parallax` if installed globally.

**Project root:** `/Users/ianburke/Documents/github/parallax-v0/`

---

## Pre-Production Checklist

Before writing any scene prompts:

- **Real brand in the brief?** Fetch their website first. Note visual style (dark vs. light, color palette, UI aesthetic). If you can screenshot a relevant product UI or branded image, save it locally and add it to `reference_images` for product/broll scenes — nano-banana accepts up to 8 reference images and the brand visual will anchor the scene aesthetic far better than a text description alone.
- **Scene length:** Keep every scene under 6 seconds (~18 words of VO max). Grok clips are fixed at ~6s — anything longer requires looping. Split rather than stretch.
- **Pacing — default to fast and kinetic.** At least 50% of scenes must have `animate: true`. In the first 8 scenes especially, no scene should exceed 4 seconds — shorter is almost always better. A static Ken Burns cut through 8 scenes with only 1–2 animated is not acceptable pacing. If in doubt, animate more, not less. Energy comes from motion.
- **Working resolution:** Always use `resolution: 1080x1920`. Frame.io is the review surface — 480p renders are never acceptable for upload. Do not use 480x854 or 720p unless the user explicitly requests a draft.

> **Stills-only briefs:** skip the brand website fetch and the scene-length and animation rules — there is no VO and no video. Focus only on prompt quality and character consistency.

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
voice: kore               # Gemini TTS voice (default: kore) — `parallax models show gemini-flash-tts`
speed: 1.1                # TTS speed multiplier
model: nano-banana        # image model alias
caption_style: bangers    # bangers | impact | bebas | anton | clean
headline: THE BIG CLAIM   # omit to skip
# captions: skip          # uncomment to disable captions
# hq: true                # use 720p for all Grok i2v clips (default: 480p); override per scene with animate_resolution

# Lock voiceover — skips regeneration
audio_path: parallax/output/v6/audio/voiceover.mp3
words_path: parallax/output/v6/audio/vo_words.json

# Character reference image for scenes with reference: true
character_image: parallax/scratch/ref.png

# Avatar (lip-sync PiP overlay)
avatar:
  image: parallax/scratch/avatar_blue_bg.png   # blue bg for chroma key
  avatar_track: parallax/output/v12/video/avatar_track.mp4          # lock after first gen
  avatar_track_keyed: parallax/output/v12/video/avatar_track_keyed.mov  # pre-keyed ProRes 4444
  track_start_s: 0.0
  position: bottom_left   # bottom_left | bottom_right | top_left | top_right
  size: 0.40              # fraction of OUTPUT frame width (0.40 = 432px on 1080p — default)
  # y_offset_pct: omit unless user asks to raise the avatar — default anchors bottom edge 20px from frame bottom
  full_audio: true        # one Aurora call for full voiceover (not per-scene)

scenes:
  - index: 0
    shot_type: broll        # broll | character | screen
    vo_text: "Words spoken here."
    prompt: "Pixar-style 3D, 9:16 vertical..."
    still_path: parallax/output/v6/stills/nano-banana_abc123.png  # lock approved still
    animate: true           # generate video clip via Grok i2v
    animate_resolution: 720p  # per-scene override; default is 480p (plan-level hq: true sets 720p globally)
    clip_path: parallax/output/v17/video/scene_00_animated.mp4   # auto-written after generation
    motion_prompt: "Slow camera drift, warm light..."
    zoom_direction: up      # up | down | left | right | in
    zoom_amount: 1.30       # zoom factor (1.0 = no zoom, 1.3 = 30% zoom in)

  - index: 1
    shot_type: broll
    vo_text: "More words."
    prompt: "..."
```

### Auto-locking — how it works

**`still_path` and `clip_path` are written automatically to the plan YAML after each generation.** You do not need to add them manually. The pipeline skips any scene that already has these fields set.

To regenerate a scene: delete its `still_path` (and `clip_path` if animated) from the YAML and re-run. Everything else stays locked.

| Field | Skips | Written automatically? |
|---|---|---|
| `still_path` | Image generation for that scene | ✓ yes — after generate_image |
| `animate: true` + `clip_path` | Grok i2v call for that scene | ✓ yes — after animate_scenes |
| `audio_path` + `words_path` | Voiceover generation (both required) | no — lock manually after first good VO |
| `avatar.avatar_track` | Aurora avatar generation | no — lock manually |
| `avatar.avatar_track_keyed` | Chroma key step | no — lock manually |

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

Avatar generation through Parallax is no longer supported. Supply a pre-recorded avatar clip via `avatar.avatar_track` in the plan and Parallax will pre-key it once to ProRes 4444 with alpha, then composite as PiP overlay on the main video.

```yaml
avatar:
  avatar_track: parallax/output/v12/video/avatar_track.mp4
  avatar_track_keyed: parallax/output/v12/video/avatar_track_keyed.mov
  chroma_similarity: 0.30   # how close to the key color counts as background (0.1 = tight, 0.4 = loose)
  chroma_blend: 0.03        # edge softness — keep ≤ 0.03 or character becomes transparent
```

- Lock `avatar_track` and `avatar_track_keyed` immediately after first successful key
- The `.mov` is ProRes 4444 with alpha channel — no chroma filter needed at composite time
- `chroma_similarity` and `chroma_blend` apply at the pre-key step (`key_avatar_track`). Once `avatar_track_keyed` is locked, they have no effect — re-key only if the edges look bad

### Avatar Audio — Pre-recorded only

**Branch A — Pre-recorded avatar (user-supplied video with audio)**
Signs: A `content/avatar_track.mp4` or similar file exists in the project folder, and the script says to use it (e.g. "use the green screen avatar audio/video", or implies a real person's footage).

Steps:
1. `ffprobe` the file to confirm it has an audio stream.
2. Extract audio at original sample rate — **never add `-ar 16000`**:
   ```sh
   ffmpeg -y -i avatar_track.mp4 -vn -ar 44100 -ac 1 parallax/scratch/avatar_audio_full.mp3
   ```
3. Set `audio_path: parallax/scratch/avatar_audio_full.mp3` in the plan.
4. Get word-level timestamps using the CLI — do not run raw whisper commands:
   ```sh
   parallax audio transcribe parallax/scratch/avatar_audio_full.mp3 --out parallax/scratch/vo_words.json
   ```
   Set `words_path: parallax/scratch/vo_words.json` in the plan.
5. **Do not call TTS at all.** The avatar audio IS the voiceover — generating TTS alongside it creates two competing audio sources and the plan will use whichever one gets written to `audio_path` last.
6. **NEVER trim an avatar track with raw ffmpeg.** H.264 re-encode changes color range metadata (`color_range=tv`) which breaks the downstream chroma key — the entire frame gets keyed out with no error. Always trim using:
   ```sh
   cd /Users/ianburke/Documents/github/parallax-v0
   uv run parallax audio trim \
     --plan "{folder}/parallax/scratch/plan.yaml" \
     --folder "{folder}" \
     --start <start_s> --end <end_s>
   ```
   This outputs ProRes `.mov` (preserves color range), extracts audio from the trimmed avatar (guaranteed in sync), and updates `plan.yaml` automatically. The word timestamps also get adjusted.
7. When the script specifies a cut point (e.g. "up till the line X"), use `parallax audio detect-silences` or word timestamps from step 4 to find the exact `end_s`, then run `parallax audio trim`.

AI-generated avatars are no longer supported through Parallax — supply a pre-recorded avatar clip per Branch A above.

Whisper is used for **word-level timestamps only** (for caption timing). If Whisper fails:
- Continue with the existing audio (whichever branch above)
- Fall back to fixed-WPM timing (~140 WPM) for captions
- Never treat a Whisper failure as a signal to generate TTS — these are separate operations

### Chroma Key — Always Detect Background Color First

Do not assume green. Sample corner pixels from the raw avatar track before keying:

```sh
parallax video color content/avatar_track.mp4 --time 2
```

Prints `0xRRGGBB` directly. Use that value for `chroma_key` in the plan. Common backgrounds: green (`0x00FF00`), blue (`0x0000FF`), teal (`0x79C3CA`), grey.

### Chroma Key Verification — Check Face Features, Not Just Presence

After keying, do two checks:

**1. Overall visibility:** Extract a full frame and confirm the avatar appears in the expected corner.

**2. Face-area crop:** Zoom into the face to verify that sunglasses, glasses, hair edges, and accessories are not partially transparent:

```sh
# Full frame check
parallax video frame output.mp4 2.0 --out /tmp/check_full.jpg
# Face crop — adjust crop coords for avatar size and position
ffmpeg -y -ss 2 -i output.mp4 -vf "crop=200:300:0:ih-350" -vframes 1 /tmp/check_face.jpg
```

Read both. If sunglasses or accessories look ghosted/see-through:
- The key color is catching reflective or dark-tinted surfaces
- **Lower `chroma_similarity` further** (try 0.08–0.12) to narrow what counts as background
- Do NOT raise `chroma_blend` — that softens edges and makes features more transparent, not less
- Re-key and check again before declaring success

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

**Stage 3 — Approve and animate:** For scenes the user approves for animation, add `animate: true`. Stills are already auto-locked from Stage 2 — do not manually add `still_path`. Re-run — only scenes with `animate: true` and no `clip_path` incur Grok costs.

**Stage 4 — Iterate:** `clip_path` is auto-locked after generation. To re-prompt a scene, delete its `still_path` and `clip_path` from the YAML and update the prompt. Everything else stays locked automatically.

```sh
# Stage 2 — stills + Ken Burns only (no animate: true in YAML yet)
parallax produce --folder ./project --plan ./project/parallax/scratch/plan.yaml

# Stage 3 — after approval, add animate: true to approved scenes
parallax produce --folder ./project --plan ./project/parallax/scratch/plan.yaml

# Stage 4 — lock clips, iterate on any remaining scenes
parallax produce --folder ./project --plan ./project/parallax/scratch/plan.yaml
```

Each run auto-increments the output version. Output lands in `parallax/output/vN/` with this structure:

```
parallax/output/vN/
  stills/          ← generated PNGs (scene images)
  audio/           ← voiceover.mp3, voiceover_raw.mp3, vo_words.json
  video/           ← ken_burns_draft.mp4, animated clips, avatar track, intermediate builds
  {id}-{slug}-vN.mp4   ← final export (at root of vN/ for upload scanner)
  manifest.yaml    ← scene timing + asset paths (OUTPUT — written by parallax produce, do not edit)
  plan.yaml        ← copy of the input plan used for this run (for audit — do not edit this copy)
  cost.json        ← pipeline spend for this run
```

For stills-only plans (add `stills_only: true` to the plan), the pipeline stops after generating stills — no voiceover, no video assembly. Output is just `stills/` with the PNGs and `cost.json`.

---

## Output Naming Convention

The final MP4 is automatically renamed to convention at the end of every run — no manual copy needed:

```
parallax/output/vN/{id:04d}-{slug}-vN.mp4
```

Convention: `{id:04d}-{slug}-v{N}.mp4` — matches what `upload_to_frameio.py` scans for. Use the zero-padded 4-digit concept ID and brand slug from `concepts.json`.

---

## Format Selection (Default Logic)

When given a script + assets, pick the format without asking. The agent decides based on what's present:

| What's provided | Format |
|---|---|
| Script only | B-roll montage — generated scenes, Ken Burns assembly |
| Script + avatar image | Avatar overlay — Aurora lip-sync, chroma key, composite over generated B-roll |
| Script + avatar + external footage | Avatar overlay — same workflow; use external footage as `clip_path` for background scenes |
| Script + external footage, no avatar | B-roll montage — use external footage as `clip_path` |
| Script has `Format: STILLS ONLY` (standard template header), OR explicit language like "no video / just images" | **Stills-only** — generate one image per scene concept, upload folder to Frame.io. No VO, no video assembly. See Stills-Only Production below. |

**Character keying is always the same technique** regardless of whether the background is generated or external: blue-screen → Aurora → ProRes 4444 alpha → FFmpeg composite. Background source is just a `clip_path` value.

---

## Character Sheet Pre-Pass

A character sheet is a 2×2 grid showing the character in 4 neutral reference poses on a plain white background. It gives the image model a richer "what does this character look like" signal than a single reference image — especially for consistency across many scenes.

**When to generate a character sheet:**
- The plan has `character_sheet: true` explicitly set, OR
- A `character_image` reference file exists AND this is the first render (not a revision) AND there are 4+ scenes featuring the character

**When NOT to generate:** revision passes (the sheet already exists from the first run), broll-only concepts, and any plan with fewer than 4 character scenes.

**Generation steps:**

1. Write `parallax/scratch/character_sheet_plan.yaml` with exactly 4 scenes on white background:
```yaml
stills_only: true
model: nano-banana
character_image: <path-to-existing-reference>  # omit if no reference image exists

scenes:
  - index: 0
    prompt: "FULL BODY, FRONT VIEW, [character description], standing upright, neutral relaxed expression, arms at sides, plain pure white background, character reference sheet style, photorealistic"
  - index: 1
    prompt: "FULL BODY, 3/4 VIEW, [character description], turned 45 degrees to the right, neutral expression, plain pure white background, character reference sheet style, photorealistic"
  - index: 2
    prompt: "HEAD AND SHOULDERS CLOSE-UP, FRONT VIEW, [character description], neutral expression, plain pure white background, character reference sheet style, photorealistic"
  - index: 3
    prompt: "HEAD AND SHOULDERS CLOSE-UP, 3/4 VIEW, [character description], turned slightly, neutral expression, plain pure white background, character reference sheet style, photorealistic"
```

2. Run: `cd /Users/ianburke/Documents/github/parallax-v0 && uv run parallax produce --folder "<concept-folder>" --plan "<concept-folder>/parallax/scratch/character_sheet_plan.yaml"`

3. Stitch the 4 generated PNGs into a 2×2 grid using this inline Python script (Pillow is always available in the parallax env):
```python
from PIL import Image
from pathlib import Path

# Paths: update to match actual generated still paths from plan.yaml
stills = [
    Path("<concept-folder>/parallax/output/v1/stills/<scene0>.png"),
    Path("<concept-folder>/parallax/output/v1/stills/<scene1>.png"),
    Path("<concept-folder>/parallax/output/v1/stills/<scene2>.png"),
    Path("<concept-folder>/parallax/output/v1/stills/<scene3>.png"),
]
out = Path("<concept-folder>/parallax/scratch/character_sheet.png")

imgs = [Image.open(p) for p in stills]
w = max(i.width for i in imgs)
h = max(i.height for i in imgs)
grid = Image.new("RGB", (w * 2, h * 2), (255, 255, 255))
for idx, img in enumerate(imgs):
    grid.paste(img.resize((w, h)), ((idx % 2) * w, (idx // 2) * h))
grid.save(out)
print(f"Character sheet saved: {out}")
```

4. Update the main plan's `character_image` to point to `parallax/scratch/character_sheet.png`.

5. **Upload the character sheet to Frame.io and block for approval.** This is mandatory — never proceed to the main render without human sign-off on the character design.

   a. Upload `parallax/scratch/character_sheet.png` to the concept's Frame.io folder using the frameio client. Use the same folder-find-or-create pattern as `upload_to_frameio.py`. Set `concept.frameio_link` to the resulting `view_url`.

   b. Call `mark_blocked.py` with a clear message:
   ```sh
   uv run scripts/mark_blocked.py --id <N> --reason "Character sheet ready for review: <frame_io_view_url>. Comment 'approved' on Frame.io to proceed with the full render, or describe any changes needed. Then flip status → ready_for_edits."
   ```

   c. Push to the sheet:
   ```sh
   uv run scripts/sheets_sync.py --push-only
   ```

   d. Tell the user: "Character sheet uploaded to Frame.io — [link]. Flip to ready_for_edits after you approve or leave feedback."

**Do not write the main plan or run the main render until the character is approved.**

### Character Approval Resume Flow

When a concept blocked at the character sheet stage is flipped back to `ready_for_edits`:

- Tick sees `frameio_link` exists → calls `pull_revisions` → fetches Frame.io comments since `last_comment_check`.
- The render session resumes with those comments in context. Read them carefully:
  - **"approved" (or "looks good", "go ahead", equivalent):** Proceed to the main render. The character sheet at `parallax/scratch/character_sheet.png` is the approved reference. Continue from step 4 above — write the main plan and run it.
  - **Change requests described in comments:** Update the character description in the character sheet plan, re-generate the sheet (re-run parallax produce on `character_sheet_plan.yaml`), re-stitch, re-upload, and block again with the new Frame.io link. Do not touch the main plan.
  - **No new comments:** `pull_revisions` will leave the concept at `ready_for_edits` with a "No new comments" log entry. The user needs to add their feedback or approval on Frame.io before the next tick picks it up.

**Important:** The 4-scene character sheet plan runs in the same parallax output directory as the main concept. Use a version-bumped subfolder or scratch to avoid clobbering main output. The simplest approach: run the character sheet plan BEFORE creating the main plan, since parallax auto-increments the version (`v1` for sheet, `v2` for main run).

---

## Stills-Only Production

Use when the brief explicitly says no video — "just images", "a collection of stills", "I don't need an ad". Output is numbered PNGs uploaded to the concept's Frame.io folder; no voiceover, no video assembly.

**Stills-only pipeline — do these steps in order:**

**Step 1 — Read the reference image (MANDATORY if one exists).** Before writing any scene prompts:
1. Check whether the concept folder has a `media/` subfolder with an image file.
2. If yes: use the Read tool to view that image file right now.
3. Write down (in a scratch comment or in your reasoning) the character's exact appearance: species, skin color, body type, clothing style, art style, any distinctive features.
4. Every scene prompt you write must describe the character based on what you saw — never from assumption or script text alone.

Skipping this causes the first generation pass to produce wrong characters, which forces a full re-run on the failed scenes. That is expensive and avoidable.

**Step 2 — Write the plan YAML** (only after completing Step 1).

**Plan YAML — stills-only shape:**
```yaml
stills_only: true
model: nano-banana
character_image: media/image1.png   # point directly to the reference file in media/ — DO NOT copy to scratch

scenes:
  - index: 0
    vo_text: "Here's what would happen if a man stopped f***ing around and fixed his sleep."  # verbatim from script — line 1
    prompt: "Character making morning coffee in a bright modern kitchen, warm tones, 9:16 vertical..."
  - index: 1
    vo_text: "Here's what would happen if a man stopped f***ing around and fixed his sleep."  # same line carries across scenes if it spans multiple stills
    prompt: "Character on a morning run through a city park, golden hour..."
  - index: 2
    vo_text: "Stop guessing. Take the quick quiz at the link in our bio..."  # verbatim from script — line 2
    prompt: "Character at a gym doing dumbbell curls, clean athletic space..."
```

- **Always include `vo_text`** when the brief has script copy — even in stills-only mode. `vo_text` is the script line the still is meant to illustrate. It posts as a Frame.io comment on upload so reviewers see which line each image represents. If the brief is genuinely copy-free (pure visual moodboard), omit it.
- **`vo_text` must be copied verbatim from the script — never invented, paraphrased, or summarised.** If the script has two lines spread across four scenes, split the actual script text at natural phrase boundaries and distribute those fragments. A scene's `vo_text` may be a mid-sentence fragment or a clause — that is correct and expected. What is never acceptable is writing copy that does not appear in the script (e.g. "Day 1. He downloads the app." when the script says no such thing).
- No `voice`, `headline`, `caption_style`, `audio_path`, `words_path`, or `animate`
- Write one prompt per "scene concept" — aim for 6–10 stills unless the brief specifies otherwise
- Use `character_image` if a reference image was provided — nano-banana accepts it for character consistency

**Run:**
```sh
cd /Users/ianburke/Documents/github/parallax-v0
uv run parallax produce --folder "<concept-folder>" --plan "<plan.yaml>"
```
Pipeline stops after generating stills (no VO, no video). Output lands in `parallax/output/vN/stills/*.png`. The plan YAML is updated with `still_path` per scene automatically.

**Upload:**
```sh
cd "/Users/ianburke/Library/CloudStorage/GoogleDrive-ian@narrativeads.com/My Drive/PARALLAX CONTENT"
uv run --with httpx --with python-dotenv scripts/upload_to_frameio.py --id N --stills
```
This reads scene order from `plan.yaml`, numbers the PNGs (`01.png`, `02.png`…), uploads them to the concept's Frame.io folder, and sets `frameio_link` + `status → ready_for_review` in `concepts.json` automatically.

**Then push to sheet:**
```sh
uv run --with google-api-python-client --with google-auth scripts/sheets_sync.py --push-only
```

**Verification:** After upload, `concepts.json` → `frameio_link` is the view URL of the first still in the Frame.io folder. Report it to the user — Frame.io shows all stills in the folder context.

**`character_image` in stills-only mode:** The pipeline automatically applies the reference image to every generated scene — you do NOT need `reference: true` on each scene. Just set `character_image` at the top level and point it at the file in `media/` (never copy it to scratch; always reference the original).

---

## Character Reference Rules (applies to all formats)

These rules prevent the most common failure mode: generating images that look like a text description of the character instead of the actual character.

**HARD RULE — missing character reference = block, never improvise.** If the script or brief says "use the character reference image" OR any scenes have `reference: true` OR a `character_image` path is specified in the plan, you MUST verify that the referenced file actually exists before generating any images. If it does not exist:

1. Call `mark_blocked.py` with a clear reason: `uv run scripts/mark_blocked.py --id N --reason "Character reference image not found. Expected at: <path>. Drop the reference image into the concept folder and flip to ready_for_edits to retry."`
2. Stop. Do not proceed with a text-only character description as a fallback.

A text description produces an inconsistent, random-looking character. A blocked concept with a clear message is far better than a completed render with the wrong character. The human can fix a missing file in seconds; fixing generated imagery requires a full re-run.

**`character_image` is NOT applied automatically in video mode.** Setting it at the top level only enables it — each scene that should feature the character also needs `reference: true` in the plan. Without it, `character_image` is silently ignored and the model generates a generic person.

```yaml
character_image: media/image1.png   # always point to media/ directly — no scratch copy

scenes:
  - index: 3
    reference: true    # ← REQUIRED for character_image to be sent to the model
    prompt: "..."
```

**Always use `media/image1.png` (or whichever file is the character reference) directly.** Never copy the reference to `parallax/scratch/ref.png` — the copy adds a failure point with no benefit. The path in `character_image` is resolved relative to the concept folder, so `media/image1.png` always works.

**When to add `reference: true`:** Any scene whose prompt describes or implies a specific character (not generic broll). If the scene says "the trainer in the gym" or "the skeleton in the suit," it needs `reference: true`. Generic background/environment scenes don't.

**Stills-only exception:** `character_image` is auto-applied to all generated stills — no `reference: true` needed.

**Reference image = character identity, not scene recreation.** When a reference image is provided, use it to capture the character's appearance, art style, and CGI aesthetic — NOT to recreate the reference scene. The background, setting, lighting, and composition of the reference image should be ignored entirely. Each scene prompt should place the character in a completely new environment as described. The only thing that should carry over is: what the character looks like (face, body, clothing style, art style). If the output looks like a tweaked version of the reference image rather than the character in a new scene, the prompt is leaning too heavily on the reference. Write prompts that describe the new environment in specific detail so the model focuses on placing the character there, not on reproducing the source image. Exception: if the user explicitly asks to "recreate" or "edit" the reference image, then match it closely.

---

**Smart defaults when inputs are ambiguous:**
- Voice not specified → use `george`
- Caption style not specified → use `bangers`
- No resolution specified → `1080x1920`
- Avatar position not specified → `bottom_left`, `size: 0.40`, no `y_offset_pct` (anchors 20px from bottom edge)
- **Avatar sizing — HARD MINIMUM `size: 0.40`.** `size` is a fraction of frame WIDTH. "Less than X% of the frame" means X% AREA. Math: `size: 0.40` = 40% width × 40% of frame height ≈ 10% frame area — well under any "less than 25%" constraint. NEVER set `size` below `0.30`. If the agent writes `size: 0.24` it is wrong regardless of what the user said.
- **`y_offset_pct` — NEVER set by default.** Omit it entirely. The pipeline anchors the avatar's bottom edge 20px from the frame bottom. Only add if the user explicitly asks to raise the avatar. `y_offset_pct: 0.24` places the bottom edge 24% up from the bottom — this is far too high and makes the avatar look like it's floating in the middle of the frame.
- **Chroma key — default `chroma_similarity: 0.30`, `chroma_blend: 0.03`.** NEVER write `chroma_similarity` below `0.20` without explicit instruction. Low values (0.10–0.15) leave background spill around the avatar edges — the key is too tight to remove the full color. Start at 0.30 and lower only if the character is getting clipped.
- No `animate` preference stated → animate scenes that are action/product shots; keep talking-head backgrounds as Ken Burns stills

---

## After Render — Default to Frame.io

Don't open the video locally unless the user explicitly asks. The default review surface is Frame.io.

After any completed render:
1. Upload:
   ```sh
   uv run scripts/upload_to_frameio.py --id <N>
   ```
2. Push to sheet:
   ```sh
   uv run --with google-api-python-client --with google-auth scripts/sheets_sync.py --push-only
   ```
3. Report the Frame.io link from `concepts.json` → `frameio_link`. Done.

---

## Frame.io Upload

All uploads go through `scripts/upload_to_frameio.py`. It handles folder lookup, clearing old files, upload, `concepts.json` update, and status flip to `ready_for_review` automatically.

```sh
# Batch — uploads all concepts with status=rendering and no frameio_link
uv run scripts/upload_to_frameio.py

# Targeted — upload concept N regardless of status or existing frameio_link (use for revisions)
uv run scripts/upload_to_frameio.py --id 3
```

The `--id` flag is the right call for any revision re-upload. Never write custom Frame.io upload code — the script handles the project root folder, per-concept subfolder creation, and old-file cleanup internally.

**Project root folder ID:** `108b8d0e-15fa-46a8-9693-e78934ac1376` (the Frame.io folder that holds per-concept subfolders — not the same as the project UUID `e8807083-...`). The script already knows this; stated here so you don't need to read the script to find it if you're debugging a raw API call.

---

## Revision Flow (end-to-end)

When told to "address comments on concept N" or similar:

1. **Read concepts.json** — confirm the concept's `frameio_link` and `folder`.
2. **Pull Frame.io comments:**
   ```sh
   cd "/Users/ianburke/Library/CloudStorage/GoogleDrive-ian@narrativeads.com/My Drive/PARALLAX CONTENT"
   uv run python3 scripts/fetch_comments.py --id N
   ```
   Output is one comment per line: `[timestamp_s]  text`. General (non-timecoded) comments show `-` as timestamp. Handles token refresh automatically. See the `frameio` skill for the underlying API details.

   > **Do not use** `frameio_revisions.py` as a CLI (no `__main__`, zero output) or `client.get_comments_summary()` (crashes on `timestamp: null`).
3. **Interpret and apply** — edit the plan YAML at `{folder}/parallax/scratch/plan.yaml`. Add/modify params, unlock scenes by deleting `still_path`/`clip_path` as needed.

   **Silence removal (e.g. "remove silence at [00:01]"):**
   ```sh
   # Read audio_path from plan.yaml first — it changes after each trim iteration
   cd /Users/ianburke/Documents/github/parallax-v0
   uv run parallax audio detect-silences "{folder}/{plan.audio_path}"
   # Then remove the target range (frame-accurate, updates plan.yaml in-place)
   uv run parallax audio trim \
     --plan "{folder}/parallax/scratch/plan.yaml" \
     --folder "{folder}" \
     --start <start_s> --end <end_s>
   ```
   The trim command:
   - Trims the avatar track to ProRes 422 `.mov` (preserves color accuracy for chroma keying — H.264 re-encode silently breaks chromakey)
   - Extracts audio FROM the trimmed avatar (guaranteed in sync — avatar is the A/V source of truth)
   - Adjusts word timestamps in the words JSON
   - Updates `plan.yaml` automatically with new versioned paths

   Do NOT manually split/concat audio or avatar tracks. Do NOT use a hardcoded `avatar_audio_trimmed.mp3` path — always read `audio_path` from the current plan.

4. **Re-run the pipeline:**
   ```sh
   cd /Users/ianburke/Documents/github/parallax-v0
   uv run parallax produce --folder "<concept-folder>" --plan "<plan-yaml>"
   ```
5. **Sample frames** to confirm the change is visible (see Verification section).
6. **Upload + flip status:**
   ```sh
   cd "/Users/ianburke/Library/CloudStorage/GoogleDrive-ian@narrativeads.com/My Drive/PARALLAX CONTENT"
   uv run scripts/upload_to_frameio.py --id N
   ```
   This uploads the latest render, updates `frameio_link`, and sets status → `ready_for_review` in `concepts.json` automatically. Prior versions are kept in the Frame.io folder so comment history is preserved.

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
- **Whisper failure ≠ TTS fallback.** Whisper is only used for word-level timestamps. If it fails, fall back to fixed-WPM caption timing and continue with the existing audio. Never treat a Whisper failure as a signal to generate new TTS — these are separate operations.
- **Never extract avatar audio at 16kHz.** Do not add `-ar 16000` when extracting audio from a pre-recorded avatar track. 16kHz is Whisper's internal processing rate — using it as the output sample rate produces muffled, radio-quality audio. Always extract with `-c:a copy` (preserving original rate) or `-ar 44100`.
- **Add a tail buffer when trimming pre-recorded avatar audio.** After computing the trim point from `last_word_end`, add 0.5s: `ffmpeg -t {last_word_end + 0.5}`. Without it, FFmpeg rounding clips the final word.
- **Chroma key color is not always green.** Always sample actual background pixels before keying. Teal, blue, and grey screens are all common. Using `0x00FF00` on a teal background silently produces a bad key that only shows up during frame inspection.
- **Never re-encode an avatar track to H.264 mid-pipeline.** H.264 changes color range metadata (`color_range=tv`) which causes `chromakey` to key out the entire frame — 100% transparent output, no error. Use ProRes 422 or FFV1 for any mid-pipeline avatar re-encode. The `parallax audio trim` command handles this correctly (outputs `.mov` ProRes automatically).

## Key FFmpeg Gotchas

- **Grok clips are 24fps, not 30fps.** Any per-frame calculation must use `t` (seconds) not `n/n_frames` to be frame-rate agnostic.
- **Grok clips have generated audio.** Always strip with `-an -c:v copy` immediately after downloading; leaving audio in will override the voiceover.
- **Black first frame in concat.** Normalize all clips to the same codec/fps/resolution before concat (`libx264 yuv420p fps=30`). Never use `-c:v copy` across clips with different codecs.
- **ProRes 4444 for alpha.** When pre-keying an avatar, use `-pix_fmt yuva444p10le -c:v prores_ks -profile:v 4444` to preserve the alpha channel.

---

## Environment Variables

| var | purpose |
|---|---|
| `OPENROUTER_API_KEY` | Required for every real-mode call (image / video / TTS) |
| `ANTHROPIC_API_KEY` | Required for agent mode (`parallax run`) |
| `PARALLAX_TEST_MODE=1` | Pillow + ffmpeg stubs — no network, no spend |
| `PARALLAX_LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` |

All TTS routes through OpenRouter (Gemini 2.5 Flash Preview TTS) — see `parallax models show gemini-flash-tts` for the voice list. Avatar generation is no longer supported through Parallax; supply pre-recorded avatar tracks via `avatar.avatar_track`.
