#!/usr/bin/env sh
# Parallax one-shot installer for macOS.
# Installs uv if missing, installs/upgrades parallax, prompts for
# OPENROUTER_API_KEY once, persists it to ~/.zshrc, and runs a smoke
# test that doesn't spend any credits.
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
  # OPENROUTER_API_KEY is the single required credential — every model
  # (image, video, TTS) routes through OpenRouter.
  if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    info "Parallax needs an OPENROUTER_API_KEY for real-mode runs."
    info "Get one at: https://openrouter.ai/settings/keys"
    info "(Skip and use PARALLAX_TEST_MODE=1 for stub-mode dry runs.)"
    OR_KEY_INPUT=$(prompt_tty "Paste your OPENROUTER_API_KEY (or press Enter to skip): ")
    if [ -n "$OR_KEY_INPUT" ]; then
      ENV_ADDS="${ENV_ADDS}export OPENROUTER_API_KEY=${OR_KEY_INPUT}
"
      export OPENROUTER_API_KEY="$OR_KEY_INPUT"
    else
      warn "No OPENROUTER_API_KEY entered — real-mode runs will fail until you set one."
      warn "  export OPENROUTER_API_KEY=sk-or-..."
      warn "Or use PARALLAX_TEST_MODE=1 for stub-mode dry runs."
    fi
  fi

  # Persist what we collected in a single marker block so the whole set is idempotent.
  if [ -n "$ENV_ADDS" ]; then
    {
      printf '\n%s\n' "$MARKER_START"
      printf '%s' "$ENV_ADDS"
      printf '%s\n' "$MARKER_END"
    } >> "$RC"
    info "Saved env vars to $RC"
  fi
fi

# 4. Smoke test — confirms the install resolved + the CLI runs.
# `parallax models list` exercises the catalog loader and CLI plumbing
# without spending credits or requiring an API key.
info "Running smoke test (no spend)..."
if PARALLAX_TEST_MODE=1 parallax models list >/dev/null 2>&1; then
  info "Smoke test passed."
else
  warn "Smoke test failed. Run manually to see the error:"
  warn "  PARALLAX_TEST_MODE=1 parallax models list"
fi

info "Done. Open a new terminal (or run: source $RC), then try:"
printf "   parallax produce --folder ./my-project --brief ./my-project/brief.yaml\n"
