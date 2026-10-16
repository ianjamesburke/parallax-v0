---
name: parallax
description: Use when operating the Parallax CLI to produce AI-generated short-form videos. Covers project setup, brief authoring, the plan→produce loop, asset locking, model selection, and cost management.
metadata:
  version: "0.5.43"
---

# Parallax CLI — Operator Skill

> **Version:** `0.5.43` — run `parallax --version` on load. If the installed version is higher, warn the user that the skill may be behind and suggest running `parallax update` to check the changelog.

Agentic creative production CLI. A `brief.yaml` goes in; a finished short-form video comes out — stills, voiceover, animated clips, captions, all routed through OpenRouter.

**Install check:**
```bash
parallax --version
parallax update   # uv tool upgrade parallax --reinstall
```

**Workflow map:** run `parallax guide` (JSON: stages, commands, flags, preconditions, review boundaries, human decisions). It is the source of truth; this skill adds only judgment on top of it.

**Required env var:**
```bash
export OPENROUTER_API_KEY=sk-or-...
```

---

## Intake: references and a social-first brief

Before planning a new production, inspect the supplied script, project notes, and reference inventory. Ask one compact set of questions only for choices that remain unresolved: whether to preserve the script verbatim, whether the user wants a storyboard or the full video, and any format, voice, or spending constraint.

Unless the user specifies another source, treat the selected project folder as the complete input boundary: use its script and resources to create the Parallax project. Pass that folder as `--folder`; Parallax creates and updates its working files under `<folder>/parallax/`. Do not search adjacent projects, archived runs, or other host folders for substitute inputs.

Choose the performance mode from the brief: voiceover with B-roll, on-screen presenter, or an explicitly requested mix. A first-person voiceover does not imply a talking avatar. For voiceover-led social videos, read [voiceover-broll.md](references/voiceover-broll.md) before storyboarding or revising shots.

When a named brand or product appears in the video, gather missing visual references from its official website or verified app-store listing as part of storyboard preparation, unless the user limits browsing or supplies a complete brand kit. Inspect and save the actual logos and useful product images with their source URLs. Show them in the storyboard as official-source references pending creative review. Preserve real logos and UI; generated approximations are not official assets. A supplied composite style sample is enough to start reference development; ask for cleaner files only when a particular shot needs them.

Plan global character/product references can merge with scene references. For mixed character shots and diagrams, prefer explicit per-scene refs and omit global character/product fields from the production plan so unrelated images do not condition graphics.

### Script structure and project identity

Separate opening display copy from spoken narration before splitting scenes. A labeled `H:` or `Headline:` section belongs in top-level `headline` when it denotes on-screen opening text; do not put it in `vo_text`. `Body:` identifies narration. A hook can be spoken when the script says so; ask only when its role is ambiguous. Verify the assembled scene narration against the body text, separately from headline preservation. The production headline currently lasts through the first scene.

Preserve an existing project title. Otherwise propose a short descriptive `concept_name` in the brief and mark it as a proposal in the review conversation. Accept an optional user-supplied `concept_id`; do not invent a client tracking identifier. The HTML heading and browser tab use this identity. Keep the longer production objective in `goal`.

### Develop reference material before scene generation

When a style sample needs translating into a consistent video character or world, generate a small reusable reference set within the authorized budget: typically a clean character sheet and one environment/style frame. Use the supplied sample to guide these assets. Include them in the storyboard before generating the scene batch, clearly marked as generated candidates pending approval. Use approved character/style assets as scene `image_refs` instead of repeatedly feeding a broad screenshot containing unrelated characters and text. Keep branding in separately sourced official assets.

Record references in `assets.provided` with `origin: supplied`, `generated`, or `official`, `review_status: pending` or `approved`, and `source_url` for web sources. `provided` means the file exists as an input, even if generated earlier in the workflow. Only record approved after real user acceptance. Reference generation is a paid preparation step; the HTML render itself remains free. Preserve original source files and record prompts, model, output and billed costs.

### Scene-length and hook check

Before spending on a brief or plan, audit the script scene by scene. A generation scene should express one readable visual beat, not a paragraph, a montage, or a sequence of camera moves. Flag a proposed scene when it has multiple locations/actions, needs a visual change to stay interesting, or would hold a single shot beyond roughly 5 seconds (and never rely on one generated clip to carry more than the selected video model supports). Offer a split into short shots—usually 1–4 seconds for fast social creative—while preserving the user's narration and meaning. Do not silently rewrite an approved script.

Do not send a compound direction to a single-image model. A diptych, split screen, before/after, collage, or “does X, then does Y” direction must become separate scene beats or an explicitly approved code-authored multi-panel graphic. A motion prompt may describe temporal change only after its still/start-frame has one clear composition. Treat this as a planning error, not an image-generation retry problem.

Treat on-screen copy as layout, not prompt prose. For vertical 9:16 work, keep every text element within title, caption, and platform-safe margins; wrap or shorten long copy before rendering. Code-authored graphics must use bounded text layout and a rendered-frame inspection. Do not repair one overflowing card by hand while leaving the template capable of producing the same error elsewhere.

If the user has provided only a concept or asks for a script from scratch, propose a concise, platform-appropriate version first: lead with a specific visual/action hook in the first 1–2 seconds, put the tension, benefit, or surprising change on screen early, vary the visual beat before attention drops, and reserve a clean final beat for the CTA. Prefer a 15–45 second initial cut unless the user specifies another duration. For a longer script, say why it needs a cut-down or a multi-shot treatment and offer concrete short-form versions before generating.

Show the native HTML storyboard before scene-batch generation; it contains the scene/reference map. Record only additional reference provenance or missing assets in project notes, avoiding a second manually maintained scene table. Shared art direction belongs in a short style note, and each executable image prompt must include the necessary style description: plan `style` and `style_hint` currently affect voice synthesis, not still generation.

Reference gates are structured CLI output, not skill prose. `parallax storyboard --json` lists each open gate with its question, allowed choices, and the exact `parallax reference resolve` command per choice. Ask the human the gate question, offer only those choices, run the chosen command. Never choose for the human or run paid reference generation without explicit authorization. `parallax produce --brief` refuses while a gate is open.

Audit visual variety before paid generation. Motion graphics should earn their place as explanatory beats, transitions, or data moments; they are not a fallback for every line of narration. Flag a storyboard when motion graphics exceed roughly 40% of scenes or when three or more appear consecutively. Propose specific editorial, product, or action alternatives before spending.

---

## Review checkpoints

For a staged production, keep the authored brief as the scene map. `scene_type` describes the proposed final treatment: `still`, `animation`, or `motion_graphic`; `shot_type` describes the content: `character`, `broll`, or `screen`. Preserve proposed treatments when creating stage-specific execution plans.

1. **Storyboard and reference direction.** Preserve the supplied narration and beat order unless the user asks for a rewrite; separate the headline first. Gather official brand assets and, when authorized, develop the small reference set above. Author concise scene prompts and explicit scene types. Run `parallax storyboard --folder /absolute/project --brief /absolute/project/brief.yaml --json`, answer any open reference gates, open `storyboard-package/storyboard.html`, and ask for feedback on scene breakdown and treatments. This command is local and free. Empty preview areas are pending assets, not generated stills. Estimated seconds are for review only.
2. **Stills.** After storyboard/reference approval and a spending cap, use the accepted reference set for a representative scene, then generate the approved batch. If reference development was deferred, review that set first. Use `stills_only: true` in a separate execution plan for image scenes. Keep unfinished graphics out of that generation batch and use the motion-graphics workflow below. Inspect and lock approved stills, update `still_path` in the brief, and refresh the HTML. Pause for still approval before paid animation.
3. **Narrated draft and motion.** After still approval, make a separate draft plan with approved stills, `scene_type: still` and `animate: false` for image scenes, `stills_only: false`, and `captions: skip`. Keep the final proposed types in the authored brief. For unfinished graphics, use the approved poster as a still in the draft plan. Preserve raw narration and inspect spoken fidelity before trusting alignment or captions. Use measured narration timing to price and prepare a small animation pilot and timed graphics. Show the pilot before extending to the remaining selected shots; carry forward approved assets and the same budget ledger.
4. **Final review.** Assemble accepted clips against approved narration, inspect captions and every scene transition, decode the export, and open the video. Report mechanical checks separately from human acceptance.

These are conversational checkpoints, not an enforced CLI approval state machine. Record the reviewed brief revision, artifact paths, user decision, spend and next authorized action in a small project review log. A simulated user response must be labeled simulated and cannot authorize actual spend or advance a real human gate. On resume, read the latest decision rather than an older handoff. Regenerate the HTML after scene or asset changes so proposed treatment and available media remain visible together.

Example scene:

```yaml
- index: 0
  shot_type: character
  scene_type: animation
  vo_text: "The supplied narration, unchanged."
  prompt: "One clear visual beat in the agreed illustration style."
  motion_prompt: "A small readable gesture; keep the camera steady."
  image_refs: [references/character.png]
```

For `scene_type: motion_graphic`, read [motion-graphics.md](references/motion-graphics.md). Drafts without that type retain legacy `animate` behavior. Avoid setting `animate` inconsistently with an explicit type.

## Core loop

Two artifacts drive every project:
- `brief.yaml` — human spec. Author once; edit between iterations.
- `plan.yaml` — engine spec. Generated by `parallax plan`; edit to lock assets.

```
brief.yaml → parallax plan → plan.yaml → parallax produce → output/vN/video.mp4
```

One-shot (plan + produce in one command):
```bash
parallax produce --folder ./my-project --brief ./my-project/brief.yaml
```

Two-step (inspect/edit the plan before spending):
```bash
parallax plan    --folder ./my-project --brief ./my-project/brief.yaml
# plan lands at ./my-project/parallax/scratch/plan.yaml — inspect, edit
parallax produce --folder ./my-project --plan ./my-project/parallax/scratch/plan.yaml
```

Non-interactive (only after the current stage is authorized):
```bash
parallax produce --folder ./my-project --brief ./my-project/brief.yaml -y
```

Resolution override (sets both output and animate resolution):
```bash
parallax produce --folder ./my-project --brief ./my-project/brief.yaml --resolution 480x854
parallax plan    --folder ./my-project --brief ./my-project/brief.yaml --resolution 480x854
```

Validate without spending:
```bash
parallax validate --folder ./my-project --brief ./my-project/brief.yaml
```

**Paths:** Always use absolute paths for `--folder`. Never construct paths relative to `$PWD` inside a worktree — it resolves by accident and breaks. Paths with spaces must be quoted.

---

## brief.yaml format

```yaml
concept_name: "My 30-Day Reset" # propose if no title exists
concept_id: null             # optional supplied tracking ID
headline: "30-day drinking reset is all it takes" # opening overlay, not spoken
goal: "30-second direct-response ad"
aspect: "9:16"          # 9:16 | 16:9 | 1:1 | 4:3 | 3:4
voice: Fenrir           # default: nova
voice_speed: 1.1        # default: 1.0
animate_resolution: null # set to e.g. "720x1280" for HQ video gen

pronunciations:
  Tongkat Ali: tongkat ali

success_criteria:
  - "All scenes use consistent character"

script:
  scenes:
    - index: 0
      shot_type: broll         # broll | character | screen
      vo_text: "[rapidly] Hook line here."
      prompt: "Cinematic shot of..."

    - index: 1
      shot_type: character
      vo_text: "[dramatically] Second beat."
      prompt: "Close-up of..."
      animate: true
      motion_prompt: "Slow zoom in"
      aspect: null             # per-scene aspect override
```

**Per-scene reference images** — pass one or more images as generation references for a specific scene:
```yaml
script:
  scenes:
    - index: 0
      shot_type: broll
      vo_text: "..."
      prompt: "..."
      image_refs:
        - brand/product.png
        - brand/product_alt.png
```

**Character consistency** — set `character_reference: true` at the brief level so every `shot_type: character` scene automatically uses `character_image` as its reference during still generation:
```yaml
character_reference: true

assets:
  provided:
    - path: brand/founder.png
      kind: character_ref   # planner resolves this to plan.character_image
```

**Providing existing footage** — add to `assets.provided`; reference in scenes via `prompt`:
```yaml
assets:
  provided:
    - path: brand/product.mp4
      kind: product_ref
      description: "Existing product footage"
```

`still_path` and `clip_path` are supported in both brief scenes and plan scenes. They lock existing assets; paths resolve from the project root. A `motion_graphic` scene can be storyboarded before its clip exists, but production requires its rendered `clip_path`.

---

## plan.yaml — production settings

Model, caption, and transition settings live in `plan.yaml` (not brief.yaml). Set them before running `parallax produce`:

```yaml
voice_model: tts-mini        # tts-mini (default) | tts-gemini
image_model: mid             # draft ($0.004) | mid ($0.009, default) | premium ($0.033) | seedream | grok-image
video_model: mid             # mid = Grok Imagine Video | draft (Seedance) | veo-lite (cheapest 720p) | premium | wan
caption_style: bangers        # bangers (default) | anton | or custom dict
fontsize: 55
words_per_chunk: smart        # tiny-word grouping | integer maximum; both stop at punctuation
caption_shift_s: 0.0
default_transition: null      # transition type
default_transition_duration_s: 0.5
stills_only: false            # true = images only; skips audio, animation, assembly
trim_pauses: true             # true | false | float (max pause seconds)
style: null                   # voice synthesis direction; not a shared image prompt
style_hint: null              # additional voice direction
resolution: null              # output resolution e.g. "480x854"
animate_resolution: null      # video gen resolution e.g. "480x854"
headline: null                # opening overlay through the first scene
headline_fontsize: null
headline_y_position: null     # top of text block; e.g. "h*18/100" (legacy default 12%)
headline_font: null           # e.g. anton; legacy default bangers
headline_bg: null             # e.g. "0x000000"
headline_color: null          # e.g. "0xFFFFFF"
captions: null                # caption mode override
caption_animation: null       # animation style for captions
titles: null                  # list of title card objects
avatar: null                  # avatar config object
voice_postprocess: null       # post-processing pipeline for voice audio
```

Caption review: preserve canonical script punctuation when matching words to measured
narration timestamps. Stop and inspect token mismatches instead of replacing unmatched
ASR text. Both chunk modes stop at sentence/phrase punctuation; `words_per_chunk: 3`
produces readable groups of at most three words. `smart` retains its mostly one-word,
four-letter grouping, so choose the integer mode for short phrases.

For restrained portrait captions, start with `caption_style: {base: anton,
y_expr: "h*64/100-th"}`, `fontsize: 44`, and `words_per_chunk: 3`.
The y expression places the text bottom at 64% of frame height, above the bottom third;
inspect the actual composition and platform controls before finalizing. Fontsize is the
1080px-wide baseline and scales with output width. Keep full caption bounding boxes
clear of faces, app UI, and key graphics; do not infer readability from a successful burn.


## plan.yaml — asset locking

After a run, `output/vN/plan.yaml` is a read-only snapshot. The working plan is `parallax/plan.yaml` (or wherever you pointed `--plan`). Lock approved assets so only changed scenes regenerate:

```yaml
# Lock a still
- index: 0
  still_path: parallax/output/v1/stills/openrouter_mid_1234567_n1080x1920.png
  vo_text: "..."

# Lock a generated clip
- index: 2
  clip_path: parallax/output/v1/video/openrouter_kling_1234567.mp4
  vo_text: "..."

# Per-scene reference controls
- index: 3
  reference: true          # use plan.character_image as the still-generation reference
  reference_images:        # explicit refs merge with applicable global refs
    - brand/hero.png
  vo_text: "..."
```

Audio locks belong at the **top level** of the plan, not inside a scene:

```yaml
audio_path: parallax/review-audio/voiceover.wav
words_path: parallax/review-audio/vo_words.json
```

Copy approved audio and timing files to these run-local paths first. Keep scene narration matched to the recording and preserve the reviewed voice speed/postprocessing settings.

Re-run to regenerate only unlocked scenes:
```bash
parallax produce --folder ./my-project --plan ./my-project/parallax/plan.yaml
```

---

## Models and utility commands

Read [commands.md](references/commands.md) for image, audio, video, inspection, and cost commands. Choose models from `parallax models list` and inspect their supported duration, references and resolution with `parallax models show`. Treat catalog prices as estimates and track billed calls, including failed/retried calls, against the agreed cap.

## Dry run (no spend)

```bash
PARALLAX_TEST_MODE=1 parallax produce --folder ./my-project --brief ./my-project/brief.yaml
```

Full pipeline runs end-to-end with stub assets. Use to validate brief structure before spending.

---

## Review and opening generated media

Treat a completed generation as something to show, not merely a path to report. Immediately open the primary artifact in an available viewer after each completed image, audio, video, storyboard, draft, or final render. For a storyboard, open the visual storyboard (or its individual stills), not just the YAML plan. For a completed video, open the video itself; for audio, open/play the audio; for an image, open the image. Report the exact artifact that was actually opened.

Before progressing from storyboard to narration, animation, or final assembly, inspect the generated visual output against the scene/reference map. Do not treat a successful command or a populated reference field as evidence of style, character, or product fidelity. Keep rejected artifacts and their diagnosis, and regenerate only the affected unlocked scenes.

---

## Output layout

```
my-project/
  brief.yaml
  parallax/
    plan.yaml             ← working plan (edit between iterations)
    scratch/
      plan.yaml           ← landing zone for `parallax plan` output
    output/
      v1/
        project-v1-<hash>.mp4
        plan.yaml         ← snapshot — do not edit
        manifest.yaml
        cost.json
        run.log
        stills/
        audio/
        video/
      v2/
        ...
```

---

## Rules

- `--folder` is the project root; all paths in `plan.yaml` are relative to it
- Never edit `output/vN/plan.yaml` — it's a snapshot
- Lock assets before re-running — unlocked scenes regenerate and cost money
- Use `tts-gemini` + `[emotional]` tags for any ad that needs pacing variation
- Choose image and video models for the approved references, resolution, shot duration and budget; check the installed catalog before spending.
- **STOP before calling `parallax image generate` with `--model draft` and a non-square aspect (9:16, 16:9, 3:4, 4:3):** `draft` sometimes ignores the aspect ratio. Use `grok-image` or `mid` instead.
- `-y` skips the CLI cost prompt; it never grants creative approval or a spending budget.
- Run `parallax validate` before `parallax produce` to catch brief/plan errors without spending credits
- After every generated artifact, open the artifact in an available viewer before declaring that stage complete
- Output flag is `--out <path>`, never `--output` — that flag does not exist.
- **macOS screenshot filenames use Unicode characters** (middle dots, narrow no-break spaces), not regular spaces — they cannot be constructed by hand. Always resolve via `find` and copy to `/tmp/` first:
  ```bash
  SRC=$(find . -maxdepth 1 -name "Screenshot*" -type f | head -1)
  cp "$SRC" /tmp/source.png
  ```
  Then pass `/tmp/source.png` to `--ref`.
