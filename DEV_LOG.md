# DEV_LOG

Ground-up rewrite of the Parallax CLI. Newest-first. Captures intentional decisions, gotchas, and deferrals that git history and code alone will not preserve.

## 2026-04-18 — [CHANGED] Distribution verified: `uv tool install` works
Live-tested `uv tool install --python 3.11 --from <repo> parallax --force` on this machine. Installed binary at `~/.local/bin/parallax`, runs from any cwd, produces `output/` relative to cwd, writes sessions/usage to `~/.parallax/`. Both backends verified from the installed binary in `/tmp` with `PARALLAX_TEST_MODE=1`. Minimal README added with the install one-liner, env-var table, and model-alias table.

**Breaks if:** `uv tool install` from this repo requires any manual venv setup, omitting `--python 3.11` succeeds silently against a lower Python (it shouldn't — pyproject.toml pins `requires-python = ">=3.11"`), or the installed `parallax` binary is missing from the user's PATH after install.

## 2026-04-18 — [GOTCHA] `uv tool install` needs `--python 3.11` on systems with older default Python
System Python on this machine is 3.9.6, so a bare `uv tool install --from <repo> parallax` fails with "Python>=3.11 required" since uv tries to match against the system interpreter rather than downloading one. The fix is `--python 3.11` (uv will download 3.11 if it doesn't have it). Documented in the README. Do not "fix" this by lowering `requires-python` — the SDKs and type hints we use (`str | None`, etc.) require 3.10+, and we deliberately pinned 3.11 as the install target per the project's Python-tooling convention.

## 2026-04-18 — [CHANGED] Reference-image support extended to `mid` (flux/dev img2img)
`ModelSpec` gained `ref_param_name: str | None` and `max_refs: int` so each model can declare both the edit endpoint's arg name and the cardinality. `fal.generate` dispatches: singular-param models get `args[param] = url_str`; list-param models get `args[param] = list_of_urls`. `tools._validate_refs` rejects over-the-limit refs at the tool boundary so the agent pivots before any upload happens.

Current support matrix: `mid` (flux/dev/image-to-image, `image_url`, max 1), `nano-banana` (gemini-25-flash-image/edit, `image_urls`, max 8). `draft`, `premium`, `grok` still raise — adding each is a pricing.py patch plus one live verify, no dispatch changes.

Rejected: a per-spec callable that shapes args arbitrarily. `ref_param_name` + `max_refs` covers the two shapes we've seen and will cover the Flux Kontext family too — we'll only need the callable if a future model wants more than refs (e.g. a separate `mask_url`), and that's an additive field, not a rewrite.

**Breaks if:** calling `generate_image(model="mid", reference_images=[<real jpg>])` with `FAL_KEY` set doesn't produce a file in `output/`, or passes `image_urls` (plural) to `fal-ai/flux/dev/image-to-image` — that endpoint only knows `image_url` and will silently ignore or error on an unknown field.

## 2026-04-18 — [CHANGED] Reference-image support (nano-banana only for v0)
`generate_image` gains an optional `reference_images: list[str]` of local paths. When supplied, we upload each via `fal_client.upload_file`, route to the model's `edit_fal_id` sibling endpoint, and pass the returned URLs as `image_urls`. Only nano-banana (`fal-ai/gemini-25-flash-image/edit`) supports this in v0 — Flux Kontext/redux/img2img and a grok edit path are per-endpoint work we'll add one at a time as demand shows up, not speculatively. Adding them later is purely additive: set `edit_fal_id` on the spec and the dispatch just works.

Unsupported-model + missing-file validation lives at the tool boundary (`tools._validate_refs`) so failures surface as `tool_result is_error=true`, not crashes. Live verify confirmed: the agent, told to use `draft` with refs, received the ValueError, stopped retrying, and correctly pivoted to recommending nano-banana. `alias_guidance()` now tells the model which aliases support refs and instructs "use one of those when the user supplies input images."

Rejected: generic "pass any FAL param through the tool" — same footgun as model IDs. Rejected for v0: Flux Kontext on `premium` — endpoint + param shape differ from nano-banana's `image_urls`, would expand the test matrix before we know which cases matter.

**Breaks if:** calling `generate_image(model="nano-banana", reference_images=[<real png>])` with `FAL_KEY` set doesn't produce an output file; or calling it with `model="draft"` plus refs doesn't raise a ValueError at the tool boundary before any FAL call.

## 2026-04-18 — [CHANGED] Real FAL integration wired (prompt-only, v0)
`generate_image` now calls FAL for real via `fal_client.subscribe` when not in test mode. `fal.generate(prompt, spec)` is the only call site; runner + downloader are dependency-injectable so hermetic tests don't touch the SDK. FAL-side failures (auth, quota, outage, safety) wrap as `RuntimeError` and surface to the model as `tool_result` errors — the agent can retry, apologize, or pivot instead of crashing the loop. `FAL_KEY` required; missing key fails fast at first real call. Deliberately prompt-only for v0 — reference-image support is purely additive at the tool-schema / pricing surface when it lands; the only non-additive work will be stripping prior-turn input bytes at the resume boundary (already flagged in an earlier DECISION entry).

Rejected: raw HTTP (reimplements queueing/polling for zero benefit); passing arbitrary FAL params through the tool schema (invites the "agent hallucinates a field" footgun we already guarded against for model IDs).

**Breaks if:** `FAL_KEY=... PARALLAX_TEST_MODE=` (unset) `uv run parallax run --backend anthropic-api --brief "..."` doesn't produce a real image file in `output/` whose extension matches the FAL-returned content type, or the usage record for that call shows `test_mode: true` / `cost_usd: 0.0`.

## 2026-04-18 — [GOTCHA] FAL returns JPEG from Flux Schnell — honor URL extension, don't assume .png
First live verify wrote a file named `draft_<hash>.png` that was actually JPEG content (Flux Schnell defaults to JPEG on FAL). Fixed by deriving the extension from the returned URL path (`.png/.jpg/.jpeg/.webp` allowed, else fallback `.png`). Don't hardcode an extension — different FAL models return different formats (and even the same model can change its default). Viewers coped, but `file` command revealed the mismatch, and downstream tools that dispatch by extension would have silently misbehaved.

## 2026-04-18 — [CHANGED] Model alias ladder + per-call cost/time tracking
Five-alias ladder (`draft`, `mid`, `premium`, `nano-banana`, `grok`) defined in `src/parallax/pricing.py`, verified against fal.ai on 2026-04-17. Agent-facing tool schema constrains `model` to `enum: list(ALIASES)`; unknown alias raises `ValueError`. System prompt carries `alias_guidance()` so the model sees descriptions + prices and knows to default to `mid`. Every `generate_image` call appends one NDJSON line to `~/.parallax/usage.ndjson` with `{ts, session_id, backend, alias, fal_id, tier, prompt_preview, output_path, duration_ms, cost_usd, test_mode}`. New `parallax usage [--include-test]` subcommand aggregates by alias and session. Test-mode records land with `cost_usd=0.0` and `test_mode=true` so you can see what a dry run would have cost without polluting real-spend totals.

Live verify caught a real bug: the MCP tool handler on the claude-code backend runs in a separate task context from the SDK message loop, so `current_session_id.set(sid)` on the outer loop did not propagate in — usage records landed with `session_id=None` on that backend. Fixed by capturing a mutable `sid_holder` dict in the tool closure and re-setting the ContextVar inside the handler. Anthropic-api backend was unaffected (single task). Lesson reinforced: ContextVars are per-task; closures over SDK-owned tasks must re-set at the boundary.

**Breaks if:** `parallax usage --include-test` after a `PARALLAX_TEST_MODE=1 parallax run` shows `0 sessions` or a `None` session_id when inspecting `~/.parallax/usage.ndjson` directly. Also breaks if passing an unknown alias (e.g. `"flux-pro"`) in a brief succeeds instead of erroring at the tool boundary.

## 2026-04-17 — [CHANGED] Runtime logging wired; default WARNING, -v INFO, -vv DEBUG
Added `src/parallax/log.py` and a `-v/-vv` flag on the CLI (also `PARALLAX_LOG_LEVEL` env). At INFO you see: backend selected, session id + session-log path, each tool call with a truncated args summary, each tool result, and — on the claude-code path — the SDK's jsonl transcript path so you know exactly where to grep when behavior is off. DEBUG adds full args, full raw results, and per-SDK-message type.

Caught via live invocation that I should have done before calling commit 1b shipped: both backends' hermetic tests were green, but neither had actually been executed end-to-end against real infra. Fixed by running test-mode `parallax run` on both backends before landing this commit. Verified: claude-code produces a real `~/.claude/projects/<sanitized-cwd>/<sid>.jsonl`, anthropic-api produces `~/.parallax/sessions/<sid>.ndjson`, and the logged SDK transcript path matches the actual file location.

**Breaks if:** `parallax -v run --backend <x> --brief "..."` with `PARALLAX_TEST_MODE=1` emits no `parallax.tools: tool call` line on stderr, or the `parallax.backends.claude_code: SDK transcript` path doesn't correspond to an existing file on disk after the run.

## 2026-04-17 — [GOTCHA] Hermetic tests are not enough — run the CLI live before claiming done
Both backends had passing hermetic tests (FakeAnthropic / injected async query_fn) but neither had been executed end-to-end against the real Anthropic API / real claude-agent-sdk until the user asked "did you run it yourself?". Reading the code and green unit tests is not the same as proving the wiring works. Going forward: every backend-touching commit must include a `PARALLAX_TEST_MODE=1 uv run parallax run --backend <each>` invocation before claiming the commit is shipped. Test shim keeps this cheap — no FAL, no spend — so there is no excuse.

## 2026-04-17 — [CHANGED] Two backends behind a dispatcher; default = Claude subscription
Added the `claude-code` backend (default) via `claude-agent-sdk`, which routes through the user's `claude` CLI and uses their subscription — no API key, no extra billing. The existing anthropic-api backend is still selectable via `--backend anthropic-api` or `PARALLAX_BACKEND=anthropic-api` for CI / non-subscription use. Dispatcher lives in `src/parallax/backends/__init__.py`; selection is explicit-arg > env > default, with fail-fast `check_available()` on the selected backend (claude CLI on PATH / ANTHROPIC_API_KEY set). No silent fallback.

The two backends have distinct session models: claude-code uses the SDK's native session resume (stored under `~/.claude/projects/`); anthropic-api uses our NDJSON store at `~/.parallax/sessions/`. Session IDs are opaque strings round-tripped by the CLI — they never mix. Rejected: unified `Backend` protocol with a single `run_turn()` method — the SDKs' loop shapes are too different (stateless `messages.create` vs. stateful async iterator over SDK events) for the abstraction to earn its keep. Two explicit backend modules + a tiny dispatcher beats a leaky interface.

The `generate_image` tool is exposed to claude-code via an in-process MCP server (`create_sdk_mcp_server`); both backends ultimately call the same `tools.dispatch_tool()`, so the Pillow test shim works identically on both paths.

## 2026-04-17 — [DECISION] Flat single-agent architecture wrapping anthropic SDK
Rejected orchestrator+subagents (Anthropic Agent SDK) for v0 — ~15x token cost per equivalent task and zero benefit until the pipeline branches or images exceed a single context window. Rejected literal `claude -p` subprocess wrapping — would require standing up an MCP server just to expose one tool, which defeats the "smallest possible" scope. The anthropic SDK's native tool_use + `cache_control` primitives give the same capability in ~100 lines with no hidden state and a loop the user can read top-to-bottom. The "claude -p wrapper" framing is honored architecturally (thin, transparent, skill-extensible) without the literal subprocess overhead.

## 2026-04-17 — [DECISION] Tool returns file paths; image bytes never enter agent context
The `generate_image` tool returns a string filesystem path — never bytes. This makes context discipline structural rather than dependent on future stripping logic: resumed sessions stay cache-warm by construction. When Phase 2 adds `reference_images` (an input, not an output), we will add explicit byte-stripping of prior-turn inputs at the resume boundary.

## 2026-04-17 — [DECISION] Session state as append-only NDJSON at ~/.parallax/sessions/
Global location chosen by the user. One NDJSON file per session; each line is a discrete event (`session_start`, `user_message`, `assistant_message`, `tool_result`, `session_resumed`, `session_end`). Resume reconstructs the messages array by walking the file. Rejected: SQLite (overkill), JSON blob rewritten per turn (loses partial-failure forensics), per-session directory (premature). `PARALLAX_SESSIONS_DIR` env var overrides the location for tests.

## 2026-04-17 — [DECISION] Test mode as a first-class runtime flag
`PARALLAX_TEST_MODE=1` swaps any external generator for a Pillow shim that renders the request parameters as readable text onto a 1024x1024 PNG. The PNG IS the receipt of exactly what the agent asked for — zero network, zero spend, fully transparent. The filename is a deterministic hash of (prompt, model) so repeated calls produce stable paths, which makes downstream test assertions cleaner. This ships in commit 1, not as future work.

## 2026-04-17 — [DECISION] generate_image tool schema is minimal for commit 1
Schema is `{prompt, model}` only. `reference_images` and `count` land in Phase 2 commits with their own tests. Keeping commit 1's tool schema minimal proves the loop wiring without entangling it with image-routing logic or multi-output bookkeeping.

## 2026-04-17 — [GOTCHA] pyright sees src-layout imports as unresolved before `uv sync`
During file creation, pyright flagged `from .sessions import …` etc. as unresolvable. They resolve correctly after `uv sync` publishes the package into the venv via the editable `parallax` install. The src-layout with `[tool.hatch.build.targets.wheel] packages = ["src/parallax"]` is correct — do not flatten the layout.

## 2026-04-17 — [FUTURE] Real FAL client lands in commit 2
`generate_image` raises `NotImplementedError` when `PARALLAX_TEST_MODE` is unset, so the only currently-usable path is the shim. Phase 2, commit 1: add the real FAL client behind the existing schema. Phase 2 subsequent commits: `reference_images`, `count`, character consistency, and context discipline for input images.
