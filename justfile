test:
    uv run pytest -v

# Install CLI from current directory (works from any worktree)
install:
    uv tool install --python 3.11 --reinstall .

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
    uv tool install --python 3.11 --reinstall .
    echo "Bumped $current → $new and reinstalled parallax"
