# Parallax

Agentic creative production CLI. A brief goes in, a finished short-form video comes out — stills, voiceover, animated clips, captions, headline, all routed through OpenRouter.

## Install

```sh
curl -LsSf https://raw.githubusercontent.com/ianjamesburke/parallax-v0/main/install.sh | sh
```

Installs uv (Python toolchain manager) and ffmpeg if either is missing, then installs parallax. If you already have both, it just runs the last step.

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
parallax image generate "a neon-lit street at night" --aspect 9:16
parallax image generate "product on white background" --model draft --out ./stills/
parallax image generate "character close-up" --ref ./refs/face.png --aspect 1:1
parallax image analyze ./stills/frame.png
parallax image analyze ./stills/frame.png "what is the dominant color palette?"
```

Other commands: `parallax usage`, `parallax credits`, `parallax log <run|latest|list>`, `parallax verify suite <dir>`, `parallax audio {transcribe,detect-silences,trim,cap-pauses,speed}`, `parallax video {frame,color}`.

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

The Claude Code agent guide for operating this CLI lives in a standalone repo: [parallax-skill](https://github.com/ianjamesburke/parallax-skill).
