# DEV_LOG

Ground-up rewrite of the Parallax CLI. Newest-first. Captures intentional decisions, gotchas, and deferrals that git history and code alone will not preserve.

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
