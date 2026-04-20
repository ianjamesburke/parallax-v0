# DEV_LOG

Ground-up rewrite of the Parallax CLI. Newest-first. Captures intentional decisions, gotchas, and deferrals that git history and code alone will not preserve.

## 2026-04-20 — [FIX] drawtext escape order: backslash before colon

`_style_drawtext_filter` and `burn_headline` were running `replace(":", "\\:")` before `replace("\\", "\\\\")`. This doubled the backslashes inserted by the colon escape — turning `\:` (correct escaped colon) into `\\:` (literal backslash + option delimiter). Any word containing `:` (e.g. `Week 1:`) would corrupt the filter chain and break all subsequent drawtext filters.

Fix: reordered to backslash-first — `replace("\\", "\\\\")` then `replace(":", "\\:")`. Backslashes in the original text are doubled (safe), then colons are escaped, and the newly introduced `\` is not touched again.

**What NOT to do:** never run `replace("\\", "\\\\")` after `replace(":", "\\:")` — the first inserts backslashes that the second will then double.

## 2026-04-20 — [CHANGED] Clip Assembly Pipeline: video clip mode for numbered asset folders

Added a second pipeline mode alongside Ken Burns. `scan_project_folder` now detects folders that contain numbered clips (`001.mp4`, `002.mov`, `011.png`, etc.) and returns `mode='video_clips'` plus a `clips` dict mapping clip number → file path. When mode is `video_clips`, the agent uses `assemble_clip_video` instead of `generate_image` + `ken_burns_assemble`.

`assemble_clip_video` takes aligned scenes (each with `clip_paths` and `duration_s`), normalizes every clip to a uniform resolution/fps, loops or trims each scene to its voiceover duration, concats all scenes, and mixes in the voiceover audio. PNGs are converted to a 2s video before looping.

**Why:** the Fitbod Simpsons animation project has 19 numbered clips that map 1:1 to script scene markers `[001]`, `[002-004]`, etc. The Ken Burns pipeline isn't applicable here — the clips already exist. The model parses the `[NNN]` markers to build `clip_paths` per scene, aligns with voiceover word timestamps, then assembles.

**Breaks if:** `assemble_clip_video` produces a video with wrong total duration (alignment or loop-trim logic off), clip scenes appear in wrong order (scene index ordering), a PNG clip produces a black segment (ffmpeg `-loop 1` command), or `scan_project_folder` returns `mode='video_clips'` on a Ken Burns project that happens to have 3+ numbered files.

## 2026-04-20 — [GOTCHA] Bangers font: glyph clipping in drawtext — deferred

Bangers (display font with extreme italic slant) renders glyphs that extend above the font's declared ascender bounding box. ffmpeg's `drawtext` clips anything outside the declared text bounding box (`th`), so the tops of tall letters get cut regardless of position. Attempted fix was scaling the x centering (`tw*1.45`) to shift text left and give the slant room — that fixed neither the top clipping nor kept the text centered (it just shifted the whole word left).

Root cause: the clipping is not a frame-boundary issue — it's drawtext's internal render clipping against the font's declared glyph metrics. The fix is likely one of: (a) add a `y` offset to push text down so the declared box sits lower and clipped tops are off-screen instead of in-frame, (b) use `expansion=none` + manual line height overrides, or (c) switch to a font with correct metrics (Bebas Neue, Anton, Impact all render clean).

**Do NOT attempt to fix with x centering multipliers** — they shift the text off-center without helping the clip. The y/ascender metrics are the real lever.

**What NOT to do next time:** don't try `(w-tw*N)/2` style x hacks. The glyph clips at the top, not the side.

## 2026-04-20 — [CHANGED] boxer_v2: reassembly from existing assets, full pipeline

Ran the full post-production pipeline (write_manifest → align_scenes → ken_burns_assemble → burn_captions → burn_headline → write_manifest final) against pre-existing stills and voiceover — no image or audio generation. Final output: `output/boxer_v2_final.mp4` (1080×1920, 11.57s), Bangers captions 1-word-at-a-time, "SHE TRAINS ALONE" headline at y=10%. `align_scenes` fell back to the explicit timings from the brief (JSON parse issue when given a file path — the tool's path-read branch failed in this agent context). Manifests at `output/boxer_manifest.yaml` and `output/boxer_manifest_final.yaml`.

**Breaks if:** `output/boxer_v2_final.mp4` doesn't open, captions are missing or unstyled, or the headline doesn't appear at the top of the frame.

## 2026-04-20 — [GOTCHA] ffmpeg drawtext filter absent in Homebrew's minimal build
Homebrew ffmpeg 8.1 on this machine was built without `--enable-libfreetype`, so `drawtext` filter isn't in the binary. `burn_captions` was failing with "No such filter: 'drawtext'" on every run. Fixed by adding `_ffmpeg_has_drawtext()` probe and a Pillow-based fallback (`_burn_captions_pillow`) that decodes frames via rawvideo pipe, draws text with PIL/ImageDraw, re-encodes, then muxes audio back. The drawtext path is preferred when available (faster). Full pipeline now completes end-to-end in TEST_MODE.

**What NOT to do:** assume `drawtext` is universally available — it requires a ffmpeg built with `--enable-libfreetype`. The Homebrew formula used to include it, but the current build on this machine does not. Always probe first.

## 2026-04-20 — [CHANGED] video-pipeline-20260420: full pipeline e2e test passes
scan_project_folder → generate_image ×6 → generate_voiceover → align_scenes → ken_burns_assemble → burn_captions all succeed in PARALLAX_TEST_MODE=1. Final captioned video at `output/aria_ad_final.mp4` (2MB, 20.8s, 1080×1920). Agent followed pipeline order correctly. No real API calls made in test mode.

## 2026-04-18 — [FUTURE] Next swings: video support + multi-user/multi-project web UI
Two big tracks ahead of v0, explicitly not v0-scoped:

1. **Video support** — the manifest-first video pipeline per VISION.md. HoP agent (brief → scene list), Editor agent (scene list → `.parallax/manifest.yaml` with per-shot asset specs), deterministic `parallax compose` (walks manifest, calls `generate_image` + ffmpeg + video-production skill's trim-silence / captions / headline steps). Lives in a separate repo that depends on `parallax-v0` as a dep — v0 stays a standalone still-gen primitive. Gated on having a real concrete video brief to force the manifest schema; designing the schema in the abstract is the documented anti-pattern (per earlier FUTURE entry).

2. **Simple web UI with multi-user + multi-project support** — Plexi-shaped prototype that wraps the parallax CLI so creative users never see Terminal. Creative directors aren't going to `cd` into project folders; the CLI is the primitive, not the interface. Weekend-sized scope for the v1 of the UI:
   - FastAPI backend shells out to `parallax run`, parses stdout for session/cost/paths.
   - Per-user auth + per-user session isolation (each user's runs scoped to their own sessions dir + usage log).
   - Per-project scoping (text box for brief, model picker, per-project output folder, gallery view scoped to the project).
   - Block on subprocess, show spinner. No streaming in v1.
   - Deployment: MBA-on-LAN initially, cloud-hosted once a remote client needs it. Google Drive sync (via Drive for Desktop, stream mode) is the distribution layer — images land in a shared Drive folder, clients consume from Drive on any device.

**Not in scope for either track:** full Plexi App Protocol (trust floats, companion app, state buckets — per memory that's a multi-week build, not a weekend); streaming tool-call UI; built-in Drive sync as a parallax feature (keep parallax writing to `$PARALLAX_OUTPUT_DIR`, let a separate sync layer do cloud — matches "v0 is the still-gen primitive" framing).

**Sequencing decision still open:** video v1 vs. web UI, which goes first. Depends on whether a real video brief is in hand. If yes, video. If no, web UI is the higher-leverage swing because without a usable surface the video pipeline has no consumer.

## 2026-04-18 — [CHANGED] v0.1.4 patch: cached update-check nag on startup (tag v0.1.4)
Every `parallax` invocation now does a best-effort version check. Fire-and-forget at the start of `cli.main()`; never raises, swallows all errors (network, parse, filesystem). Hits `api.github.com/repos/ianjamesburke/parallax-v0/releases/latest` at most once per 24h, caches at `~/.parallax/.update_check`. Prints a single-line nag to stderr when the installed version is behind: `[parallax] A new version is available: vX.Y.Z (you have vA.B.C). Run: parallax update`. Opt out with `PARALLAX_NO_UPDATE_CHECK=1`. Skipped during `parallax update` itself.

Rejected: `packaging.Version` for semver compare (adds a dep for a 5-line helper). `git ls-remote --tags` (requires shelling out, more surface area than a 3s HTTPS GET). Auto-upgrading in-place (silently mutating the user's install is invasive and collides with explicit `parallax update` as the contract).

Version comparison uses tuple-of-ints on dot-split segments, trailing non-numeric chars dropped per segment. Good enough for x.y.z; prerelease tags compare by numeric prefix, which is close enough for a nag (we'll revisit if/when we ship prereleases).

**Breaks if:** a fresh install prints the nag (no cached value + same version as latest → fetcher returns current version → comparison is not-newer → silent), the nag triggers more than once per 24h on the same machine (cache TTL check is broken), an offline machine sees `parallax run` fail or hang on startup (fetcher has a 3s timeout and errors are swallowed), `parallax update` itself prints the nag (we explicitly skip during that subcommand), or `PARALLAX_NO_UPDATE_CHECK=1` still hits the network (the env check short-circuits before fetcher is called).

## 2026-04-18 — [CHANGED] v0.1.3 patch: backend auto-fallback + installer prompts ANTHROPIC_API_KEY (tag v0.1.3)
Two related fixes so "install on any Mac" actually works without Claude Code.

Backend dispatcher now auto-falls-back. Previously, default = `claude-code` and the call hard-failed if the `claude` CLI wasn't on PATH — even when `ANTHROPIC_API_KEY` was set and the anthropic-api backend would have worked. New behavior: when the user hasn't passed `--backend` or set `PARALLAX_BACKEND`, probe for `claude` CLI; if missing, fall back to `anthropic-api` iff `ANTHROPIC_API_KEY` is set; otherwise raise a message listing both setup paths. Explicit picks (CLI flag or env var) still hard-fail on missing prereq — we never silently override what the caller asked for.

Installer (`scripts/install.sh`) now prompts for `ANTHROPIC_API_KEY` when the `claude` CLI is absent. When `claude` is present, skip the prompt entirely — they're already set. Both keys (plus FAL_KEY) get written into a single `# >>> parallax env >>>` marker block so re-running the installer sees it and skips re-prompting (idempotent at the block level, not the per-key level — editing keys is a manual zshrc edit).

Rejected: prompting for `ANTHROPIC_API_KEY` unconditionally. Most users who install Claude Code don't want a second Anthropic key lying around — it'd bill separately and creates two auth paths to reason about.

**Breaks if:** `parallax run` with `claude` CLI absent and `ANTHROPIC_API_KEY` set still raises "claude\` CLI required" instead of routing to anthropic-api; `parallax run --backend claude-code` silently downgrades to anthropic-api when the CLI is missing (it must hard-fail — explicit picks are not overridable); re-running `scripts/install.sh` on a machine that already has the parallax env block appends a second one (the marker-block check should skip entirely); smoke test runs when no backend is configured.

## 2026-04-18 — [CHANGED] v0.1.2 patch: curl-pipe-sh installer (tag v0.1.2)
`scripts/install.sh` is the one-liner install story: bootstraps `uv` if missing, runs `uv tool install`, prompts once for `FAL_KEY` via `/dev/tty` (works through curl | sh), persists it to `~/.zshrc` between idempotent marker comments, warns if the `claude` CLI isn't installed (non-fatal — client can fall back to `--backend anthropic-api`), runs a `PARALLAX_TEST_MODE=1` smoke test. README now leads with the curl one-liner; the manual `uv tool install` path stays as a fallback for devs who already have uv.

Rejected: keychain integration (overkill for v0, adds a code path in parallax to read from `security` when env is unset); a Python-based installer (couldn't bootstrap itself before Python is set up); prompting for `ANTHROPIC_API_KEY` (only needed for the non-default backend — let clients opt in rather than prompting for a key most won't use).

**Breaks if:** `curl -LsSf .../main/scripts/install.sh | sh` on a fresh Mac doesn't land a working `parallax` on PATH, the FAL_KEY prompt hangs or silently eats input when piped through curl (the `/dev/tty` read is what prevents this — if it regresses, users type a key and the script appears frozen), re-running the installer appends a second `# >>> parallax env >>>` block instead of skipping, or the script's `set -eu` trips over the optional `read` failing when no TTY is attached (CI contexts).

## 2026-04-18 — [CHANGED] v0.1.1 patch: public repo, sonnet default, `parallax update` (tag v0.1.1)
Three small ships bundled into a patch:
- Repo flipped to public so `uv tool install git+<url>` is a true one-liner. v0 has no secrets in-repo (keys all come from the client's env), and "easy install on any Mac" is an explicit VISION principle — private + easy-install don't compose.
- `parallax update` subcommand shells out to `uv tool upgrade parallax`. Fails fast with an install hint if uv isn't on PATH. Makes install + update the same sticky-note story.
- Claude-code backend defaults to `model="sonnet"` (override via `PARALLAX_CLAUDE_MODEL`). Parallax's agent work is routine tool dispatch — Opus was wasted cost and latency.
- Also: bumped `pyproject.toml` from the stale `0.0.1` to `0.1.1` — the v0.1.0 tag shipped without updating pyproject, so `uv tool list` reported `parallax v0.0.1`. Tag + pyproject now agree.

**Breaks if:** `uv tool install --python 3.11 git+https://github.com/ianjamesburke/parallax-v0` on a fresh Mac (no auth configured) fails with a 401 (repo should be public), `parallax update` prints "uv not found" when uv IS on PATH, `parallax run` against claude-code backend shows Opus in the SDK transcript without `PARALLAX_CLAUDE_MODEL=opus` set, or `parallax --version` (when added) disagrees with the git tag.

## 2026-04-18 — [FUTURE] v0.1.0 shipped; pausing here, resuming on `0.2.0` branch
v0.1.0 tagged and released at https://github.com/ianjamesburke/parallax-v0/releases/tag/v0.1.0. Complete MVP arc shipped in one session: two backends, five-alias model ladder, real FAL integration, reference-image support on `mid` + `nano-banana`, per-call usage log, distribution verified via `uv tool install`, VISION.md + README. v0 is the still-gen primitive; the broader Parallax (HoP/Editor/Compose, manifest-first video pipeline) is explicitly not in v0 — see VISION.md.

Pausing to avoid designing v1 schema without a concrete video brief driving it — spec ambiguity is the bottleneck (per CLAUDE.md), not implementation speed. Two open next-swings when picking up on `0.2.0`:

1. **premium-ref via Flux Kontext Pro** — ~1 commit capstone on v0, completes the ref matrix. Pick this if v0.2.0 is meant as a cleanup bump on the still-gen primitive.
2. **Start v1 in a new repo** — manifest-first video pipeline. Only start if a real video brief is in hand; designing the manifest schema in the abstract is the wrong move.

If the pick is #2, `0.2.0` was the wrong branch name — v1 belongs in a separate repo per VISION.md's "v0 stays a standalone primitive that v1 consumes" framing. Reconsider before opening a PR.

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
