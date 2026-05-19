#!/usr/bin/env bash
# Remove CLI binary and venv for any PR build whose GitHub PR is no longer open.
# Reports orphaned worktrees. Requires gh CLI.
set -euo pipefail

found=0
for venv in "$HOME"/.parallax-pr-*/; do
    [[ -d "$venv" ]] || continue
    num=$(basename "$venv" | sed 's/\.parallax-pr-//')
    state=$(gh pr view "$num" --json state -q '.state' 2>/dev/null || echo "NOTFOUND")
    if [[ "$state" != "OPEN" ]]; then
        found=1
        echo "PR #$num ($state) — cleaning..."
        rm -rf "$venv"
        rm -f "$HOME/.local/bin/parallax-pr${num}"
        echo "  done"
    else
        echo "PR #$num (OPEN) — skipping"
    fi
done

if [[ $found -eq 0 ]]; then
    echo "Nothing to clean"
fi

# Clean orphaned bin entries (venv may already be gone)
for bin in "$HOME"/.local/bin/parallax-pr*; do
    [[ -f "$bin" ]] || continue
    num=$(basename "$bin" | sed 's/parallax-pr//')
    if [[ ! -d "$HOME/.parallax-pr-${num}" ]]; then
        rm -f "$bin"
        echo "Removed orphaned binary: $bin"
    fi
done
