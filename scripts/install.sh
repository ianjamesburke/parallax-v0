#!/usr/bin/env sh
# Parallax one-shot installer for macOS.
# Installs uv if missing, installs/upgrades parallax, prompts for FAL_KEY once,
# persists it to ~/.zshrc, and runs a smoke test in test mode.
#
# Usage:
#   curl -LsSf https://raw.githubusercontent.com/ianjamesburke/parallax-v0/main/scripts/install.sh | sh

set -eu

REPO_URL="https://github.com/ianjamesburke/parallax-v0"
RC="$HOME/.zshrc"
MARKER_START="# >>> parallax env >>>"
MARKER_END="# <<< parallax env <<<"

info()  { printf '\033[1;34m[parallax]\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[parallax]\033[0m %s\n' "$*" >&2; }
fatal() { printf '\033[1;31m[parallax]\033[0m %s\n' "$*" >&2; exit 1; }

# 1. uv
if ! command -v uv >/dev/null 2>&1; then
  info "Installing uv (Python toolchain manager)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # The uv installer writes PATH entries to shell rc files but not this shell.
  for candidate in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    [ -d "$candidate" ] && PATH="$candidate:$PATH"
  done
  export PATH
  command -v uv >/dev/null 2>&1 || fatal "uv installed but not on PATH. Open a new terminal and re-run."
fi

# 2. parallax
info "Installing parallax from $REPO_URL..."
uv tool install --python 3.11 --force "git+$REPO_URL"

# 3. FAL_KEY — prompt once, persist to zshrc, skip if already set
if [ -z "${FAL_KEY:-}" ] && ! grep -qF "$MARKER_START" "$RC" 2>/dev/null; then
  info "Parallax needs a FAL_KEY to generate real images."
  info "Get one at: https://fal.ai/dashboard/keys"
  printf "Paste your FAL_KEY (or press Enter to skip and use test mode): "
  # Read from the controlling TTY so this works when piped via curl | sh.
  FAL_KEY_INPUT=""
  if [ -r /dev/tty ]; then
    read -r FAL_KEY_INPUT < /dev/tty || true
  fi
  if [ -n "$FAL_KEY_INPUT" ]; then
    {
      printf '\n%s\n' "$MARKER_START"
      printf 'export FAL_KEY=%s\n' "$FAL_KEY_INPUT"
      printf '%s\n' "$MARKER_END"
    } >> "$RC"
    export FAL_KEY="$FAL_KEY_INPUT"
    info "Saved FAL_KEY to $RC"
  else
    warn "No FAL_KEY entered — real image generation will fail until you set one."
    warn "Add it later: echo 'export FAL_KEY=...' >> $RC"
  fi
fi

# 4. Claude CLI check (default backend). Non-fatal — they can use --backend anthropic-api instead.
if ! command -v claude >/dev/null 2>&1; then
  warn "The 'claude' CLI is not installed — the default backend won't work."
  warn "Install Claude Code (https://claude.com/claude-code) or run parallax with --backend anthropic-api and ANTHROPIC_API_KEY set."
fi

# 5. Smoke test — test mode, no FAL spend, no Claude login required (anthropic-api path skipped).
info "Running smoke test (PARALLAX_TEST_MODE=1, no spend)..."
if command -v claude >/dev/null 2>&1; then
  if PARALLAX_TEST_MODE=1 parallax run --brief "install smoke test: one small red cube" >/dev/null 2>&1; then
    info "Smoke test passed."
  else
    warn "Smoke test failed. Run manually to see the error:"
    warn "  PARALLAX_TEST_MODE=1 parallax run --brief 'hello'"
  fi
else
  info "Skipping smoke test (requires 'claude' CLI for the default backend)."
fi

info "Done. Open a new terminal (or run: source $RC), then try:"
printf "   parallax run --brief 'a red cube, cheapest option'\n"
