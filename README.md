# Parallax

Agentic creative production CLI. A brief goes in, a finished short-form video comes out — stills, voiceover, animated clips, captions, headline, all routed through OpenRouter.

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

Versioning is SemVer 0.x.y: `x` bumps on breaking CLI changes, `y` on additive ones. The DEV_LOG entries tagged `[CHANGED]` / `[FIX]` are the changelog.

### Shell completion

Tab completion for subcommands and flags is dynamic, driven by `argcomplete`. One command writes a cache file and tells you the one line to add to your shell config:

```sh
parallax completions install
```

It auto-detects zsh / bash from `$SHELL`, writes the stub to `~/.cache/<shell>/parallax-completion.<shell>`, and prints the `source` line to drop into `~/.zshrc` (or your dotfiles' zshrc). Restart the shell and Tab completion works. To refresh after upgrading argcomplete, `rm` the cache file and run `parallax completions install` again.

Shell startup cost: zero Python — just a `source` of the cached file. Tab-time cost is parallax startup itself.

## Setup

```sh
export OPENROUTER_API_KEY=sk-or-...
```

That is the only required env var. Everything (image, video, TTS, LLM) routes through OpenRouter.

For dry runs without spending: `export PARALLAX_TEST_MODE=1` — the pipeline produces stub PNGs / mp4s / mp3s end-to-end with no network calls.

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

## Storyboard package

`parallax guide` prints the whole workflow as JSON (stages, commands, flags, preconditions, review boundaries, what needs a human decision). `parallax guide <topic>` prints one slice (a stage id, or `produce_loop`, `asset_locking`, `models`, `cost`, `motion_graphic`, `voiceover_broll`). An agent needs only `--help`, `parallax schema`, and the guide; there is no separate operator skill.

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

The editorial page (style A) is the storyboard standard: a letter-landscape cover, then one scene per page with its uncropped 9:16 frame, narration, copy, timing, treatment, position bar, and footer. Add `--pdf` to render that same HTML as `storyboard.pdf`; if no Chrome/Chromium or Playwright Chromium is available, HTML remains written and the command explains how to install a renderer.

To re-storyboard a finished cut without any provider calls, use `--from-video` and ffmpeg:

    parallax storyboard --folder ./my-project --from-video ./my-project/final.mp4 --scenes ./my-project/scenes.yaml --pdf

`scenes.yaml` (YAML or JSON) has top-level `title`, optional `version`, and `scenes`. Each scene needs `id` and `title`; optional fields are `narration`, `on_screen_text`, `treatment`, `motion`, `start`, `end`, `frame_at`, and `kicker`. Without `start`/`end`, detected shots are assigned in order and an unequal count fails with the detected cut times. Explicit boundaries are snapped to a nearby cut (within 0.25 seconds) and misses are recorded in `handoff.json`. The package records source-video duration and SHA-256, and extracts package-relative scene frames.

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
