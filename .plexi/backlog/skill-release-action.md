# GitHub Action: trigger parallax-skill PR on new release

When a new parallax-v0 release is published, fire a `repository_dispatch` event to
`ianjamesburke/parallax-skill` that opens a reminder issue: "New parallax release vX.Y.Z — review SKILL.md for changes."

Phase 2 (deferred): add an LLM pass that diffs the CLI surface against SKILL.md and
proposes specific edits in the PR body.

## What's needed
- Release workflow in parallax-v0 (`.github/workflows/release.yml`) fires `repository_dispatch` to parallax-skill on tag push
- Workflow in parallax-skill listens for the event and opens a GitHub issue via `gh issue create`
- Requires a PAT with `repo` scope stored as a secret in both repos

## Why deferred
LLM pass complexity — the reminder-issue approach ships value now without it.
