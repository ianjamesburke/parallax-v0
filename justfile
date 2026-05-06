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

# Remove isolated PR install (run from worktrees/alpha/)
pr-clean pr:
    #!/usr/bin/env bash
    set -euo pipefail
    rm -rf "$HOME/.parallax-pr-{{pr}}"
    rm -f "$HOME/.local/bin/parallax-pr{{pr}}"
    echo "Cleaned up parallax-pr{{pr}}"

# Bump version and commit (default: patch). Usage: just bump [patch|minor|major]
bump part="patch":
    #!/usr/bin/env bash
    set -euo pipefail
    current=$(grep '^version' pyproject.toml | sed 's/version = "\(.*\)"/\1/')
    IFS='.' read -r major minor patch <<< "$current"
    case "{{part}}" in
        major) new="$((major + 1)).0.0" ;;
        minor) new="$major.$((minor + 1)).0" ;;
        patch) new="$major.$minor.$((patch + 1))" ;;
        *) echo "Unknown part: {{part}}. Use patch, minor, or major." >&2; exit 1 ;;
    esac
    sed -i '' "s/version = \"$current\"/version = \"$new\"/" pyproject.toml
    uv lock
    git add pyproject.toml uv.lock
    git commit -m "chore: bump version to $new"
    echo "Bumped $current → $new"

# Bump version + regenerate CHANGELOG via git-cliff, then commit (run from worktrees/alpha/)
# Usage: just release [patch|minor|major]
release part="patch":
    bash scripts/release-version.sh "{{part}}"

# Promote to next channel: alpha→beta or beta→main (run from worktrees/alpha/ or worktrees/beta/)
# Usage: just promote [beta|main]
promote to="":
    bash scripts/promote.sh "{{to}}"

# Bump patch version and reinstall main parallax CLI (run from worktrees/alpha/)
bump-and-install:
    #!/usr/bin/env bash
    set -euo pipefail
    current=$(grep '^version' pyproject.toml | sed 's/version = "\(.*\)"/\1/')
    IFS='.' read -r major minor patch <<< "$current"
    new="$major.$minor.$((patch + 1))"
    sed -i '' "s/version = \"$current\"/version = \"$new\"/" pyproject.toml
    uv lock
    git add pyproject.toml uv.lock
    git commit -m "chore: bump version to $new"
    uv tool install --python python3.11 --reinstall .
    echo "Bumped $current → $new and reinstalled parallax"
