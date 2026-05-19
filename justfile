test:
    uv run pytest -v

# Install CLI from current directory (works from any worktree)
install:
    uv tool install --python python3.11 --reinstall .

# Install PR as isolated CLI: parallax-pr<N> (run from feature worktree)
pr-install pr:
    #!/usr/bin/env bash
    set -euo pipefail
    install_dir="$HOME/.parallax-pr-{{pr}}"
    bin_path="$HOME/.local/bin/parallax-pr{{pr}}"
    mkdir -p "$(dirname "$bin_path")"
    uv venv "$install_dir"
    uv pip install --python "$install_dir/bin/python" .
    printf '#!/usr/bin/env bash\nexec "%s/bin/parallax" "$@"\n' "$install_dir" > "$bin_path"
    chmod +x "$bin_path"
    echo "Installed parallax-pr{{pr}}"
    echo "Test with: parallax-pr{{pr}} --help"

# Remove isolated PR install (run from the repo root)
pr-clean pr:
    #!/usr/bin/env bash
    set -euo pipefail
    rm -rf "$HOME/.parallax-pr-{{pr}}"
    rm -f "$HOME/.local/bin/parallax-pr{{pr}}"
    echo "Cleaned up parallax-pr{{pr}}"

# Remove all PR builds whose GitHub PR is no longer open.
pr-clean-merged:
    bash scripts/pr-clean-merged.sh

# Bump version, regenerate CHANGELOG via git-cliff, and commit. Defaults to patch.
# Run after merging a PR to alpha, before promoting to beta.
#   just bump           — patch bump
#   just bump minor     — minor bump
#   just bump major     — major bump
bump bump="patch":
    bash scripts/release-version.sh "{{bump}}"

# Promote to next channel: alpha→beta or beta→main (run from the repo root or worktrees/beta/)
# Usage: just promote [beta|main]
promote to="":
    bash scripts/promote.sh "{{to}}"
