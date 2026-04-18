# Parallax

Minimal agentic image-generation CLI. A creative brief goes in; image files come out. Five FAL model aliases, two backends (your Claude subscription or a raw Anthropic API key), per-call cost + time tracking.

See `VISION.md` for scope and non-goals.

## Install

One-liner — installs `uv` if missing, installs parallax, prompts for `FAL_KEY`, runs a smoke test:

```sh
curl -LsSf https://raw.githubusercontent.com/ianjamesburke/parallax-v0/main/scripts/install.sh | sh
```

Manual, if you already have `uv`:

```sh
uv tool install --python 3.11 git+https://github.com/ianjamesburke/parallax-v0
```

From a local checkout:

```sh
uv tool install --python 3.11 --from /path/to/parallax-v0 parallax
```

## Setup

Parallax auto-selects whichever backend is available — you only need one:

- **Claude subscription (preferred):** have the `claude` CLI installed and logged in.
- **Anthropic API key:** `export ANTHROPIC_API_KEY=...`. Used automatically if the `claude` CLI isn't on PATH, or when you pass `--backend anthropic-api` explicitly.

For real image generation: `export FAL_KEY=...` (get one from fal.ai). Without it, set `PARALLAX_TEST_MODE=1` to use the Pillow shim — no network, no spend, images still land in `output/` for flow verification.

The install one-liner above prompts you for these keys and persists them to `~/.zshrc`.

## Use

```sh
parallax run --brief "A watercolor cat at premium tier"
parallax run --brief "Same but oil painting" --resume <session-id>
parallax usage
parallax update   # upgrade to the latest release via uv
```

Flags:

- `-v` / `-vv` — log tool calls / full SDK events to stderr.
- `--backend {claude-code,anthropic-api}` — or set `PARALLAX_BACKEND`.
- `--resume <id>` — continue a prior session.

## Model aliases

| alias | FAL model | ~price | reference images |
|---|---|---|---|
| `draft` | flux/schnell | $0.003 | — |
| `mid` | flux/dev | $0.025 | 1 |
| `premium` | flux-pro/v1.1 | $0.04 | — |
| `nano-banana` | gemini-2.5-flash-image | $0.039 | 8 |
| `grok` | xai/grok-imagine | $0.02 | — |

Passing `reference_images=[<local path>, ...]` routes to the model's edit endpoint when supported; otherwise the call rejects at the tool boundary.

## Environment

| var | purpose |
|---|---|
| `FAL_KEY` | required for real generation |
| `ANTHROPIC_API_KEY` | required for `--backend anthropic-api` |
| `PARALLAX_TEST_MODE=1` | use the Pillow shim instead of calling FAL |
| `PARALLAX_BACKEND` | default backend selection |
| `PARALLAX_CLAUDE_MODEL` | claude-code backend model (default: `sonnet`) |
| `PARALLAX_LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` (overridden by `-v`/`-vv`) |
| `PARALLAX_SESSIONS_DIR` | override `~/.parallax/sessions/` |
| `PARALLAX_USAGE_LOG` | override `~/.parallax/usage.ndjson` |
| `PARALLAX_OUTPUT_DIR` | override cwd-relative `output/` |

## Dev

```sh
uv sync
uv run pytest
```

Opt-in live FAL test (one real `draft` call, ~$0.003):

```sh
FAL_KEY=... PARALLAX_LIVE_FAL=1 uv run pytest tests/test_fal.py::test_live_draft_call_writes_png
```
