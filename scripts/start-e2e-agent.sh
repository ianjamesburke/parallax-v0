#!/usr/bin/env bash
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
tree=$(git write-tree)

if [[ -n "${PARALLAX_E2E_ROOT:-}" ]]; then
    workspace_root=$PARALLAX_E2E_ROOT
    if [[ -e "$workspace_root" ]] && [[ -n "$(find "$workspace_root" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
        echo "PARALLAX_E2E_ROOT must be empty: $workspace_root" >&2
        exit 1
    fi
    mkdir -p "$workspace_root"
else
    workspace_root=$(mktemp -d "${TMPDIR:-/tmp}/parallax-e2e.XXXXXX")
fi

LC_ALL=C git -C "$repo_root" archive --format=tar "$tree" | LC_ALL=C tar -xf - -C "$workspace_root"

project_root="$workspace_root/fixtures/e2e-agent/default-project"
if [[ ! -f "$project_root/PROJECT.md" ]]; then
    echo "E2E fixture is missing its default project: $project_root" >&2
    exit 1
fi

git -C "$workspace_root" init -q
git -C "$workspace_root" add -A
git -C "$workspace_root" -c user.name=parallax-e2e -c user.email=parallax-e2e@local commit -qm "E2E fixture snapshot"

prompt=$(<"$workspace_root/fixtures/e2e-agent/kickoff-prompt.md")

echo "E2E workspace: $workspace_root"
echo "Codex project: $project_root"
echo "This workspace contains only the Git-index snapshot. It does not include local runs or ignored files."

if [[ "${PARALLAX_E2E_DRY_RUN:-}" == "1" ]]; then
    exit 0
fi

exec codex -C "$project_root" \
    --add-dir "$workspace_root" \
    --sandbox workspace-write \
    --ask-for-approval never \
    "$prompt"
