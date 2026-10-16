#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 [--script PATH] [--character-ref PATH] [--product-ref PATH] [--reference PATH]" >&2
}

script_path=""
declare -a reference_paths=()
declare -a reference_kinds=()

while (($# > 0)); do
    case "$1" in
        --)
            shift
            ;;
        --script)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            [[ -z "$script_path" ]] || { echo "--script may be supplied once" >&2; exit 2; }
            script_path=$2
            shift 2
            ;;
        --character-ref|--product-ref|--reference)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            reference_paths+=("$2")
            reference_kinds+=("${1#--}")
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage
            exit 2
            ;;
    esac
done

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

if [[ -n "$script_path" ]]; then
    [[ -f "$script_path" ]] || { echo "Script not found: $script_path" >&2; exit 1; }
    cp "$script_path" "$project_root/script.md"
fi

if ((${#reference_paths[@]})); then
    mkdir -p "$project_root/references"
    {
        printf '# Supplied references\n\n'
        printf '| Kind | Fixture file |\n'
        printf '| --- | --- |\n'
    } > "$project_root/references.md"

    for i in "${!reference_paths[@]}"; do
        source_path=${reference_paths[$i]}
        kind=${reference_kinds[$i]}
        [[ -f "$source_path" ]] || { echo "Reference not found: $source_path" >&2; exit 1; }
        filename=$(basename "$source_path")
        destination="$project_root/references/$filename"
        [[ ! -e "$destination" ]] || { echo "Duplicate fixture reference filename: $filename" >&2; exit 1; }
        cp "$source_path" "$destination"
        printf '| %s | `references/%s` |\n' "$kind" "$filename" >> "$project_root/references.md"
    done
fi

git -C "$workspace_root" init -q
git -C "$workspace_root" add -A
git -C "$workspace_root" -c user.name=parallax-e2e -c user.email=parallax-e2e@local commit -qm "E2E fixture snapshot"

parallax_cmd="uv run --frozen --project $workspace_root parallax"
prompt="$(<"$workspace_root/fixtures/e2e-agent/kickoff-prompt.md")

Run Parallax from this checkout, never an installed copy: \`$parallax_cmd\` stands in for \`parallax\` everywhere."

echo "E2E workspace: $workspace_root"
echo "Codex project: $project_root"
echo "Parallax command: $parallax_cmd"
echo "This workspace contains only the Git-index snapshot. It does not include local runs or ignored files."

if [[ "${PARALLAX_E2E_DRY_RUN:-}" == "1" ]]; then
    exit 0
fi

exec codex -C "$project_root" \
    --add-dir "$workspace_root" \
    --sandbox workspace-write \
    --ask-for-approval never \
    "$prompt"
