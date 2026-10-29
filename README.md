# Parallax

Agentic creative production CLI. A brief goes in, a finished short-form video comes out — stills, voiceover, animated clips, captions, headline, all routed through OpenRouter.

## Start here (agents)

```sh
curl -LsSf https://raw.githubusercontent.com/ianjamesburke/parallax-v0/main/install.sh | sh   # install
parallax --help       # every command
parallax guide        # how to author a brief and drive the pipeline
parallax guide cut    # cut a folder of existing clips to a script + voiceover + XML export
PARALLAX_TEST_MODE=1 parallax produce --folder ./my-project --brief ./my-project/brief.yaml   # free stub run, try this first
parallax update       # upgrade later
```

`OPENROUTER_API_KEY` is optional. Many commands run without it (see [Setup](#setup)).

## Install

```sh
curl -LsSf https://raw.githubusercontent.com/ianjamesburke/parallax-v0/main/install.sh | sh
```

Installs uv (Python toolchain manager) and ffmpeg if either is missing, then installs parallax with every dependency pinned to the repo's `uv.lock` (`just install` does the same from a checkout). If you already have both, it just runs the last step.

From a local checkout:

```sh
uv tool install --python 3.11 --from /path/to/parallax-v0 parallax
```

Installs are snapshot-frozen — the CLI never auto-updates from the repo. Check what you're running and pull the latest manually:

```sh
parallax --version    # version of the installed CLI
parallax update       # uv tool upgrade parallax --reinstall (pulls from the original source)
```

If you installed from a local checkout (editable install), `parallax update` runs `git pull --ff-only` in that repo first, then reinstalls. If the checkout has uncommitted changes or can't fast-forward, it touches nothing and tells you to run `git pull` yourself.

Versioning is SemVer 0.x.y: `x` bumps on breaking CLI changes, `y` on additive ones. The DEV_LOG entries tagged `[CHANGED]` / `[FIX]` are the changelog.

### Shell completion

Tab completion for subcommands and flags is dynamic, driven by `argcomplete`. One command writes a cache file and tells you the one line to add to your shell config:

```sh
parallax completions install
```

It auto-detects zsh / bash from `$SHELL`, writes the stub to `~/.cache/<shell>/parallax-completion.<shell>`, and prints the `source` line to drop into `~/.zshrc` (or your dotfiles' zshrc). Restart the shell and Tab completion works. To refresh after upgrading argcomplete, `rm` the cache file and run `parallax completions install` again.

Shell startup cost: zero Python — just a `source` of the cached file. Tab-time cost is parallax startup itself.

## Setup

`OPENROUTER_API_KEY` is **optional**. It only unlocks paid generation: AI images, AI video, TTS voices, and new LLM-written motion scenes. All of that routes through OpenRouter.

```sh
export OPENROUTER_API_KEY=sk-or-...   # only needed for paid generation
```

These work with no key and no provider calls:

- `parallax storyboard`: storyboard packages, HTML previews, optional PDF.
- `parallax ingest`: local WhisperX transcription with word timestamps (the first run may download model weights).
- `parallax cut`: script-anchored clip assembly, caption burn, and XML editor export when `--audio`/`--words`, `--plan-only`, `--from-manifest`, or a cached voiceover is used (only uncached TTS synthesis uses `OPENROUTER_API_KEY`).
- `parallax video ...` local tools: `assemble` clips with voiceover and captions, `ken-burns`, `text-overlay`, `app-screen`, `end-card`, `diagram`, and `motion --from-spec` / `--from-html` re-renders.
- `parallax export editor`: an xmeml v4 and FCPXML 1.10 timeline, assets, and SRT captions for Premiere, Resolve, and Final Cut.
- `parallax validate`, `plan`, `guide`, and `schema`.

Anything that generates with a provider (`produce`, `image generate`, `video animate`, `audio voiceover`, or `cut` when synthesizing a new TTS voiceover) needs the key, and so does `credits`.

For dry runs of the paid pipeline without spending: `export PARALLAX_TEST_MODE=1` — the pipeline produces stub PNGs / mp4s / mp3s end-to-end with no network calls.

## Use

The two iteration artifacts are `brief.yaml` (human spec) and `plan.yaml` (engine spec). Author the brief once with all your creative intent — goal, voice, aspect ratio, scene scripts — and let the planner materialize it into a plan.yaml.

```yaml
# my-project/brief.yaml
goal: "30-second product launch"
aspect: "9:16"          # 9:16 | 16:9 | 1:1 | 4:3 | 3:4 — drives framing, prompts, ref-image cropping
voice: nova
script:
  scenes:
    - index: 0
      vo_text: "..."
      prompt: "..."
```

```sh
parallax plan    --folder ./my-project
parallax produce --folder ./my-project --plan ./my-project/parallax/scratch/plan.yaml

# or one-shot:
parallax produce --folder ./my-project --brief ./my-project/brief.yaml
```

Paid commands (`produce`, `image generate`, `image analyze`, `video animate`, `audio voiceover`) print their estimated cost before the provider call and then proceed. There is no approval step or budget file; watch the estimate, and set a limit on your OpenRouter key as your own backstop. `PARALLAX_TEST_MODE=1` spends nothing.

Iterate by editing `plan.yaml` between runs. Lock approved stills with `still_path:`, audio with `audio_path:` + `words_path:`, animated clips with `clip_path:` — locked assets are reused and only changed scenes regenerate. Per-scene `aspect:` overrides the brief-level value when a single scene needs a different shape.

`--aspect <ratio>` exists on `produce` as an ad-hoc override (e.g. re-render the same brief at 16:9 once without editing it). Don't use it as the primary way to set aspect — put it in the brief.

Browse the model catalog:

```sh
parallax models list                    # tier + named aliases per modality
parallax models show mid --kind image
parallax models show tts-mini           # full TTS voice list
```

Index existing footage into a searchable JSON:

```sh
parallax ingest ./clips/                # writes clips/index.json with per-clip word timestamps
parallax ingest video.mov --estimate    # dry-run cost report
```

Generate a standalone image or analyze an existing one:

```sh
parallax image generate "a neon-lit street at night" --aspect 9:16 --folder ./my-project
parallax image generate "product on white background" --model draft --out ./stills/ --folder ./my-project
parallax image generate "character close-up" --ref ./refs/face.png --aspect 1:1 --folder ./my-project
parallax image analyze ./stills/frame.png
parallax image analyze ./stills/frame.png "what is the dominant color palette?"
```

Other commands: `parallax usage`, `parallax credits`, `parallax log <run|latest|list>`, `parallax verify suite <dir>`, `parallax audio {transcribe,detect-silences,trim,cap-pauses,speed}`, `parallax video {frame,color}`.

### Motion graphics styles

`scene_type: motion_graphic` supports a schema-constrained HTML/SVG + GSAP motion-graphics renderer (`src/parallax/motion_primitives.py`) with five built-in style presets (`flat-explainer`, `neo-brutal`, `glass-ui`, `editorial-clinical`, `kinetic-type`) alongside the zero-spend PIL primitives (`app-screen`, `end-card`, `diagram`, `text-overlay`). The LLM (`google/gemini-2.5-flash`) emits a compact JSON `SceneSpec` (`<out>.spec.json`) which Python deterministically compiles into centered, caption-safe `1080x1920` HTML/SVG + GSAP (`#safe-stage` `940x1240` inside `x=70..1010, y=80..1320 < 1344`):

```sh
parallax video motion --list-styles
parallax video motion --style flat-explainer --brand brand.yaml --text "Stop phone scams before they reach you" --words words.json --duration 4 --out scene.mp4
parallax video motion --from-spec scene.spec.json --brand brand.yaml --words updated_words.json --duration 4.5 --out scene.mp4
parallax video motion --from-html scene.html --words updated_words.json --duration 4.5 --out scene.mp4
```

In `brief.yaml` or `plan.yaml`, set `scene_type: motion_graphic` and `motion_style: flat-explainer` (with top-level `brand:`); `parallax produce` generates the `SceneSpec` + HTML (or re-times `html_path`), audits layout safety, renders the MP4 clip after voiceover alignment, and writes `html_path` and `clip_path` back into `plan.yaml`.

### Editor handoff package (Premiere Pro / DaVinci Resolve / Final Cut Pro)

Export a self-contained, portable folder from any produce run's `manifest.yaml` (defaults to the latest `parallax/output/vN/`):

```sh
parallax export editor --folder ./my-project
parallax export editor --folder ./my-project --music ./bgm.mp3 --music-db -18 --brand ./my-project/brand.yaml --out ./my-project/editor-handoff
```

Writes `<folder>/editor-handoff/` (override with `--out`):
- `timeline.xml` — FCP7-style `xmeml` v4 sequence (imports into Premiere Pro and DaVinci Resolve)
- `timeline.fcpxml` — FCPXML 1.10 sequence (toggle with `--fcpxml/--no-fcpxml`)
- `assets/` — portable copies of `voiceover.<ext>`, `scene_NN_<kind>.<ext>` (renders Ken Burns MP4s for still scenes by default; pass `--stills-as-is` to keep still images), optional `music.<ext>`, and optional `logo.<ext>`
- `captions/captions.srt` — SubRip subtitle file chunked from `vo_words.json`
- `reference/` — optional copy of the finished MP4 from the produce run
- `README.txt` & `handoff.json` — import instructions and machine-readable frame/second manifest

### Cut existing clips to a script (`parallax cut`)

When you already have a folder of clips and a script, `parallax cut` produces the voiceover (or reuses cached/supplied audio), aligns script tokens to word timestamps, computes midpoint-of-gap scene boundaries with per-clip capacity redistribution and reuse-trim continuation, writes `<project>/manifest.yaml`, renders a captioned MP4 from that manifest, and exports an editor handoff package (`<project>/assets/timeline.xml` + `timeline.fcpxml` + `captions.srt`) from the **same** manifest so the video and XML timeline always match:

```sh
# 1. Dry-run plan (zero paid calls, zero renders; exits 2 with a MAPPING NEEDED report + template if unmapped)
parallax cut --script ./script.txt --clips ./clips/ --map ./cut-map.yaml --out ./final.mp4 --plan-only

# 2. Full run (synthesizes or reuses cached TTS at 1.15x with <=0.25s sentence silence, renders MP4 + XML export)
parallax cut --script ./script.txt --clips ./clips/ --map ./cut-map.yaml --out ./final.mp4

# 3. Key-free run with supplied voiceover audio and word timings
parallax cut --script ./script.txt --clips ./clips/ --map ./cut-map.yaml --audio ./vo.mp3 --words ./words.json --out ./final.mp4

# 4. Re-render and re-export after hand-editing manifest.yaml
parallax cut --from-manifest ./final.cut/manifest.yaml --out ./final.mp4
```

**Agent workflow (`parallax guide cut`):**
1. Inspect the clips in `--clips` using your own vision (or `parallax ingest ./clips/ --estimate`). Parallax never calls a model to guess visual clip-to-script matching.
2. Provide `--map cut-map.yaml` (or omit `--map` when the script has inline `[Scene_01]` / `Scene 1:` markers or numbered clips matching the paragraph or sentence count; otherwise `parallax cut` exits `2`, prints a `MAPPING NEEDED` report, and writes `<project>/cut-map.template.yaml`).
3. Run `parallax cut ... --plan-only` to verify anchors, slot durations, clip reuse trims, and any overruns before spending or rendering.
4. Run `parallax cut ...` to produce `<out>`, `<project>/manifest.yaml`, and `<project>/assets/timeline.xml` (verified with `consistency: manifest==xml ok`).

**Mapping format (`cut-map.yaml`):**

```yaml
version: 1
segments:               # in script order
  - anchor: "45 AD."    # >=1 opening word where this segment starts (matched forward-only from previous anchor)
    clip: Scene_01.mp4  # filename, stem, or relative path inside --clips
    in: 0.0             # optional in-point seconds (default: continue where previous use of this clip ended)
    note: "Opening hook"
  - anchor: "Desperate,"
    clip: Scene_02
```

**Idempotent voiceover cache, chunked hygiene & Drive-safe encode:**
- **Chunked TTS & per-chunk cache (`--vo-chunk-seconds 60`, `--vo-gap 0.25`)**: Long scripts are split at paragraph/sentence boundaries (pass `--vo-chunk-seconds 0` for a single TTS call). Each chunk is cached raw (`<project>/cache/vo/chunks/<sha256>.raw.{mp3,json}`) and processed (`<proc_key>.proc.{mp3,json}`). Editing a single sentence re-synthesizes only that chunk; changing `--speed` (default `1.15`), `--max-sentence-silence` (default `0.25`), or `--vo-lufs` (default `-16`) re-processes cached raw chunks locally with **zero** new TTS calls. Pass `--force` to bypass caches.
- **Energy-based pause trim**: Sentence pauses are trimmed only at the **midpoint** of real acoustic silence intervals (`silencedetect` `-35dB`, `>=0.12s`) inside word-alignment gaps, keeping `>=0.15s` lead-in/lead-out (`2x` margin around tokens aligned `>0.6s`) and expanding numerals (e.g. `97%` -> `ninety seven percent`) for alignment matching while keeping script text (`97%`) in captions and the manifest.
- **Loudness normalization & consistency check (`--vo-lufs -16`, `--vo-lufs-tolerance 1.5`, `--vo-normalize/--no-vo-normalize`)**: Each chunk is two-pass normalized with EBU R128 `loudnorm` (`I=-16:TP=-1.5:LRA=11`) before concatenation. Integrated LUFS is measured per chunk (post-normalisation) and on the final concatenated voiceover, written to `<project>/voiceover.loudness.json`, and a non-fatal warning is emitted if any chunk deviates from the mean chunk loudness by more than `--vo-lufs-tolerance` (default `1.5` LU).
- **Script verification & retry (`--vo-verify/--no-vo-verify`, `--vo-retries 2`, `--strict-vo`)**: Post-TTS and post-trim audio are verified against the script. Provider omissions trigger up to `--vo-retries` per-chunk regenerations (saving failed takes as `.rejected`), while trim-induced word losses automatically escalate pause-trim safety margins (`0.15s -> 0.30s -> 0.50s -> untrimmed`). If chunks still fail after retries, `cut` exits `1`, names them and renders nothing (`--strict-vo` is the default; `--no-strict-vo` renders anyway with a warning). Default voice is `tts-gemini` / `Charon`.
- **Drive-safe MP4 encode (`--encode-preset drive-safe|source`)**: Both `parallax cut` and `parallax video assemble` default to `--encode-preset drive-safe` (H.264 `high@4.0`, `yuv420p`, CFR, `-bf 2 -g 48`, even dimensions, stereo 48kHz AAC-LC, `+faststart`, stripped encoder metadata) for reliable Google Drive, QuickTime, and iOS preview playback.

## Storyboard package

`parallax guide` prints the whole workflow as JSON (stages, commands, flags, preconditions, review boundaries, what needs a human decision). `parallax guide storyboard` is the concise storyboard-package topic. An agent needs only `--help`, `parallax schema`, and the guide; there is no separate operator skill.

    parallax storyboard --folder ./my-project --brief ./my-project/brief.yaml --json

Writes `./my-project/storyboard-package/` (override with `--package-dir`), plain visible files an editor can open without Parallax:

    brief.yaml          authored brief, verbatim
    script.md           headline and per-scene narration
    references.yaml     reference inventory, gate decisions, open gates
    scenes.yaml         scene contract with timing
    storyboard.html     offline review document
    contact-sheet.jpg   supplied stills in scene order (only when stills exist)
    qa-notes.md         open gates and review advisories
    handoff.json        editor-facing manifest; also the --json output
    assets/             referenced files at their brief-relative paths

No provider calls, no `.parallax` directory, no job or approval state. Assets must live under `--folder`. The package records proposals, never approval.

Style A is the standard editorial package: a letter-landscape cover, then one scene per page with its uncropped 9:16 frame, narration, copy, timing, treatment, position bar, and footer. HTML is always written. PDF is opt-in (`--pdf`); it needs Chrome/Chromium or Playwright Chromium. If no renderer is found, HTML remains written and the command exits with: `uv pip install playwright && playwright install chromium` (or install Chrome).

To re-storyboard a finished cut without any provider calls, use `--from-video` and ffmpeg:

    parallax storyboard --folder ./my-project --from-video ./my-project/final.mp4 --scenes ./my-project/scenes.yaml --pdf

`scenes.yaml` (YAML or JSON) has top-level `title`, optional `version`, and `scenes`. Each scene needs `id` and `title`; optional fields are `narration`, `on_screen_text`, `treatment`, `motion`, `start`, `end`, `frame_at`, and `kicker`. Keep IDs quoted when leading zeros matter. For example:

```yaml
title: Lunavia pool party
version: v3
scenes:
  - id: "00"
    title: Opening headline
    treatment: Poolside establishing shot
    kicker: ESTABLISHING
    start: 0
    end: 2.4
  - id: 12a
    title: Detail beat
```

Without `start`/`end`, detected shots are assigned in order and an unequal count fails with the detected cut times. Explicit boundaries are snapped to a nearby cut (within 0.25 seconds); change it with `--cut-threshold`, and misses are recorded in `handoff.json`. The package records source-video duration and SHA-256, extracts package-relative scene frames, and does not create a synthetic `brief.yaml` or planning advisories. After each review round, update `scenes.yaml` and re-run the exact `rerun_after_review` command in `handoff.json` (or repeat the command above).

`handoff.json` `status` is `needs_reference_decision` while a reference gate is open. Each gate carries the question, the allowed choices (`supplied`, `official`, `generated_anchor`, `no_reference`) and the exact command for each. Answers are written into brief.yaml, with comments preserved:

    parallax reference resolve --folder F --brief B --gate editorial-reference --choice supplied --asset refs/style.png

`generated_anchor` records the human's paid-generation authorization only; generate the anchor with `parallax image generate`, then attach it with `--asset`. The gate stays open until the file is attached. `parallax produce` refuses to start while any gate is open, with `--brief` or `--plan` (plans record their source brief). Files in the package use `<project-folder>` and `<brief.yaml>` placeholders instead of machine paths; the `--json` output has exact commands. `storyboard --check` previews status, gates and advisories as JSON without writing a package. Non-blocking findings are all `advisories` in the manifest (per-scene, pacing, variety). Advisories for compound generated-image directions (a still is one composition unless `scene_type: motion_graphic` selects a local composite) and unsafe 9:16 copy appear in the manifest, QA notes, and HTML. Simulated-operator proof: `tests/test_drive_parallax.py` with `fixtures/drive-parallax/`.

## Vision

A creative brief goes in; a finished short-form video comes out — without manually wrangling 5 separate APIs, 3 storage buckets, and a fragile bash glue script.

The bet: every model worth using is on OpenRouter, every iteration artifact wants to be a YAML the agent and the human can both edit, and every step worth running needs a deterministic CLI behind it. The mock-mode pipeline (`PARALLAX_TEST_MODE=1`) means an agent can plan and assemble a full video in seconds without spending a cent — paid mode just swaps the providers underneath.

What v0 ships:
- Single-provider router (OpenRouter only — no fal, no ElevenLabs, no Google direct).
- `brief.yaml` → `parallax plan` → `plan.yaml` → `parallax produce` loop.
- Three model tiers per modality (`draft`/`mid`/`premium`) plus named-alias overrides; capabilities + voice lists in `src/parallax/models/`.
- Aspect ratio first-class (top-level on `brief.yaml` / `plan.yaml`; per-scene override; `--aspect` CLI flag for ad-hoc overrides).
- Mock-mode pipeline that mirrors paid-mode end-to-end for fast iteration and CI.

What it explicitly does NOT do (yet):
- No GUI, no web UI.
- No multi-user / team coordination.
- No brand presets or campaign orchestration — those live in a separate narrative-parallax repo on top.
- No avatar/talking-head generation (no OpenRouter equivalent for the previous fal-ai/aurora path).

## Dev

```sh
uv sync
uv run pytest -q
uv run parallax verify suite tests/fixtures/verify_suite_smoke/
```

`DEV_LOG.md` is the canonical record of architectural decisions, gotchas, and deferrals — newest-first, `Breaks if:` lines on every shipped change.

## Hermetic agent intake test

Run `just e2e-agent` to start Codex in a temporary default project. The launcher builds the workspace from the Git index, so ignored local runs and untracked project files are not copied into the test. It launches Codex from that project with an intake-only prompt; the agent learns Parallax from `parallax --help` and `parallax guide`.

Pass a script and reference images as named arguments. The launcher copies them into the fixture, so the agent sees the inputs but not their original host paths:

```sh
just e2e-agent \
  -- \
  --script /absolute/path/to/script.md \
  --character-ref /absolute/path/to/character.webp \
  --product-ref /absolute/path/to/product.jpg
```

This isolates the fixture data. It does not stop an agent that deliberately reads absolute paths outside the fixture. Use a container or VM when the test also needs a hard host-filesystem boundary.
