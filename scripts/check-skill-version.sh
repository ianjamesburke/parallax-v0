#!/usr/bin/env bash
# Checks that parallax-v0-skill/skill.md version matches the CLI version in pyproject.toml.
# Run after bumping the version in pyproject.toml.

set -euo pipefail

SKILL_FILE="${1:-/Users/ianburke/Documents/GitHub/parallax-v0-skill/skill.md}"

cli_version=$(grep '^version' pyproject.toml | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
skill_version=$(grep '^version:' "$SKILL_FILE" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')

if [[ "$cli_version" == "$skill_version" ]]; then
  echo "✓ Skill in sync with CLI ($cli_version)"
else
  echo "✗ Version mismatch — CLI: $cli_version, skill: $skill_version"
  echo "  Update version in $SKILL_FILE and push."
  exit 1
fi
