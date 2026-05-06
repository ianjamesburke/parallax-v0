#!/usr/bin/env bash
# Bump version, regenerate CHANGELOG via git-cliff, and commit.
# Usage: scripts/release-version.sh [patch|minor|major]
# Default: patch
# After this, run: just promote [beta|main]
set -euo pipefail

REPO_ROOT=$(dirname "$(git rev-parse --git-common-dir)")
TREE="${REPO_ROOT}/worktrees/alpha"

die() { echo "error: $*" >&2; exit 1; }

bump="${1:-patch}"
case "$bump" in
    patch|minor|major) ;;
    *) die "unknown bump type '$bump' — must be: patch | minor | major" ;;
esac

git -C "$TREE" diff --quiet && git -C "$TREE" diff --cached --quiet \
    || die "alpha has uncommitted changes — commit first"

command -v git-cliff >/dev/null 2>&1 || die "git-cliff not found — brew install git-cliff"

current=$(grep '^version' "$TREE/pyproject.toml" | sed 's/version = "\(.*\)"/\1/')
IFS='.' read -r major minor patch <<< "$current"

case "$bump" in
    patch) new="$major.$minor.$((patch + 1))" ;;
    minor) new="$major.$((minor + 1)).0" ;;
    major) new="$((major + 1)).0.0" ;;
esac

echo "Bumping $current → $new ($bump)..."

sed -i '' "s/^version = \"$current\"/version = \"$new\"/" "$TREE/pyproject.toml"
(cd "$TREE" && uv lock --quiet)

echo "Generating changelog..."
(cd "$TREE" && git-cliff \
    --config cliff.toml \
    --unreleased \
    --tag "v$new" \
    --prepend CHANGELOG.md)

git -C "$TREE" add pyproject.toml uv.lock CHANGELOG.md
git -C "$TREE" commit -m "chore: release v$new"

echo ""
echo "v$new committed on alpha."
echo "Next: just promote beta"
