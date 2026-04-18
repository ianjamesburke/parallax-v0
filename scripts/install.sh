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

# Prompt helper — reads from controlling TTY so curl | sh works, returns "" on skip.
prompt_tty() {
  _prompt="$1"
  _reply=""
  printf '%s' "$_prompt"
  if [ -r /dev/tty ]; then
    read -r _reply < /dev/tty || true
  fi
  printf '%s' "$_reply"
}

# 3. Collect env additions — skip wholesale if parallax markers already present.
ENV_ADDS=""

if grep -qF "$MARKER_START" "$RC" 2>/dev/null; then
  info "Parallax env block already present in $RC — not re-prompting. Edit the file directly to update keys."
else
  # FAL_KEY (required for real image gen)
  if [ -z "${FAL_KEY:-}" ]; then
    info "Parallax needs a FAL_KEY to generate real images."
    info "Get one at: https://fal.ai/dashboard/keys"
    FAL_KEY_INPUT=$(prompt_tty "Paste your FAL_KEY (or press Enter to skip and use test mode): ")
    if [ -n "$FAL_KEY_INPUT" ]; then
      ENV_ADDS="${ENV_ADDS}export FAL_KEY=${FAL_KEY_INPUT}
"
      export FAL_KEY="$FAL_KEY_INPUT"
    else
      warn "No FAL_KEY entered — real image generation will fail until you set one."
    fi
  fi

  # Backend auth — if claude CLI is present, you're set. If not, prompt for ANTHROPIC_API_KEY
  # so parallax's auto-fallback can route through the raw API backend.
  if command -v claude >/dev/null 2>&1; then
    info "Claude Code CLI detected — parallax will use your Claude subscription by default."
  else
    if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
      info "The 'claude' CLI is not installed."
      info "Parallax can fall back to the raw Anthropic API if you provide an API key."
      info "Get one at: https://console.anthropic.com/settings/keys"
      info "(Alternatively, install Claude Code: https://claude.com/claude-code)"
      ANTHROPIC_KEY_INPUT=$(prompt_tty "Paste your ANTHROPIC_API_KEY (or press Enter to skip): ")
      if [ -n "$ANTHROPIC_KEY_INPUT" ]; then
        ENV_ADDS="${ENV_ADDS}export ANTHROPIC_API_KEY=${ANTHROPIC_KEY_INPUT}
"
        export ANTHROPIC_API_KEY="$ANTHROPIC_KEY_INPUT"
      else
        warn "No ANTHROPIC_API_KEY entered and no 'claude' CLI — parallax will not be able to run until you set up one of the two."
      fi
    fi
  fi

  # Persist everything we collected in a single marker block so the whole set is idempotent.
  if [ -n "$ENV_ADDS" ]; then
    {
      printf '\n%s\n' "$MARKER_START"
      printf '%s' "$ENV_ADDS"
      printf '%s\n' "$MARKER_END"
    } >> "$RC"
    info "Saved env vars to $RC"
  fi
fi

# 4. Smoke test — test mode, no FAL spend. Auto-selects whichever backend is available.
info "Running smoke test (PARALLAX_TEST_MODE=1, no spend)..."
if command -v claude >/dev/null 2>&1 || [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  if PARALLAX_TEST_MODE=1 parallax run --brief "install smoke test: one small red cube" >/dev/null 2>&1; then
    info "Smoke test passed."
  else
    warn "Smoke test failed. Run manually to see the error:"
    warn "  PARALLAX_TEST_MODE=1 parallax run --brief 'hello'"
  fi
else
  info "Skipping smoke test (no backend configured yet — install Claude Code or set ANTHROPIC_API_KEY)."
fi

info "Done. Open a new terminal (or run: source $RC), then try:"
printf "   parallax run --brief 'a red cube, cheapest option'\n"
