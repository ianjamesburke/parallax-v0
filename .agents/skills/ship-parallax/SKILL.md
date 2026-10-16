---
name: ship-parallax
description: Ship a Parallax stint end to end: pick a stint, implement in a wtp worktree with a Sonnet agent, verify, open a PR to alpha, run skill-free operator e2e against the PR build, merge, release, install, update TRUTH.md. Use for "ship", "next stint", or "run the e2e".
---

# Ship Parallax

Repo: `~/Documents/GitHub/parallax-v0`. Integration branch `alpha`. Never `main`/`master` as a base.

## 0. Read state
- Read `TRUTH.md` first (`## Next` is the next action). Resolve it: verify landed, do it, or ask Ian to drop it.
- `stint ls`. Pick the top `ready` stint (priority p1 > p2 > p3, then size s before l). Pick by hand if Ian named one.
- Human decision needed only for: money, release/promote, dropping a stint, architecture that is hard to undo. Otherwise decide.

## 1. Stint
- `stint start <id>` (or the `implement-stint-generic` skill if the stint CLI asks for it).
- Check the spec: `.stint/tasks/<id>-*.md` "Done When" and "Out of Scope". If the spec is wrong, stop and say so.
- Goal check (CLAUDE.md "Chasing rabbits"): state the limiting factor from TRUTH.md "Goal and limiting factor". If the stint does not move it, stop.

## 2. Worktree
- `git fetch -q && git log origin/alpha..HEAD` (must be empty before branching).
- `wtp add -b stint-<id>-<slug> origin/alpha`. Never raw `git worktree`.
- Verify base: `git -C <abs worktree path> log --oneline -1` equals `origin/alpha`.

## 3. Implement
- One Sonnet agent owns the worktree. Brief it with: the stint file path, the worktree path, `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` commit trailer, keys unset (`env -u OPENROUTER_API_KEY`), no paid commands, push branch only, no PR, no version bump, one-line status when quiet, stop and report on spec disagreement.
- Never dispatch a second agent into that worktree.
- Lead verifies: `git diff origin/alpha..HEAD --stat`; full suite `env -u OPENROUTER_API_KEY uv run --frozen --python 3.11 pytest -q`; grep every removed name; read any changed guide/schema text against the code.

## 4. Operator e2e (skill-free)
Goal: prove the installed CLI, with no skill and no source reading, can run the workflow the stint touched.
- Install the branch in an isolated tool dir (same as `just install`, locked):
  `uv export --frozen --no-hashes --no-emit-project --no-dev -o $C` then `UV_TOOL_DIR=<d>/t UV_TOOL_BIN_DIR=<d>/bin uv tool install --python python3.11 --constraints $C <worktree>`.
- Scenario folder: copy `script.md` and reference images from `scratchpad/scen/<A|B|C|D>/` into a fresh `ws<N>/project`.
- Operator brief (write fresh per round, one file): skill-free; crawl only `--help`, `parallax guide`, `parallax guide <topic>`, `parallax schema`; do not read `src/`, `tests/`, `docs/`, DEV_LOG; run as `env -i HOME=$HOME PATH=<tool bin>:/usr/bin:/bin parallax ...`; `PARALLAX_TEST_MODE=1` for produce; never provider calls; keep `FRICTION.md` (quote exact error text); report steps reached, friction, guide-vs-behavior mismatches, provider-call count (must be 0).
- Launch as Sonnet with `run_in_background`. Read its report and `FRICTION.md` yourself; spot-check one artifact (plan.yaml, storyboard HTML) before trusting it.
- Fix every guide gap or misleading message the operator logs, on the same branch. Re-run one operator round after fixes.

## 5. PR
- Commit in logical units with trailers. Push. `gh pr create --head <branch> --base alpha --title ... --body ...`; body ends `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Return the PR URL in the reply. Never `--delete-branch` on merge (worktree holds it).

## 6. Merge, release, install
- Sleep ~15s, then `gh pr merge <n> --squash`. `git pull --ff-only origin alpha` in the root.
- `wtp rm <branch>`; `git branch -D <branch>`; `stint done <id> </dev/null`.
- `just bump` (patch by default; minor only if roadmap says). `git push origin alpha`.
- `just install`; `parallax --version` must equal the bump.
- Check the installed build with one command from the stint's guide text.

## 7. TRUTH.md
- Same turn as every repo change: overwrite `## Next` with one action; add the PR number, merge SHA and release version to the status section.

## 8. Report
- Lead with the call. Include PR URL, merged SHA, version, suite count, operator result, anything for Ian to decide. End with `RECOMMENDATION:` and one call.

## Rules
- Operators and implementers never see API keys; spend is never triggered by agents.
- Peer agents never edit each other's worktree.
- Pre-existing known gap: `just pr-install` does not use the uv.lock constraints (`justfile` line ~27). Fix it in a stint before relying on PR installs for dependency-sensitive changes.
