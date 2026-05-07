---
name: ship-parallax
description: Use when shipping a feature or fix to the parallax Python CLI — finds an unblocked issue, implements on a feature branch, installs as an isolated parallax-pr<N> command for live CLI testing, and merges to alpha after verification.
---

# Ship Parallax

The full development lifecycle for the parallax CLI. Three entry points:

| Invocation | Entry point |
|---|---|
| `/ship-parallax` | Auto-find next unblocked issue (P1→P2→P3→P4) → Phase 0 |
| `/ship-parallax <issue-number>` | Start from a specific issue → Phase 1 |
| `/ship-parallax P1` / `/ship-parallax P2` / etc. | Find first unblocked issue at that level → Phase 0 |

---

## Phase 0 — Find the Issue

Run in parallel:
```bash
head -150 DEV_LOG.md
gh issue list --state open --json number,title,labels,body --limit 10
```

**Filter client-side — skip any issue labeled `in progress`, `blocked`, or `P0`.**

**`in progress` = another agent is actively working on that issue. Do NOT pick it up. Do NOT "continue" it. Treat every `in progress` issue as if it does not exist.**

**Determine target:**
- No arg → from the remaining issues, pick the highest-priority one (P1→P2→P3→P4), lowest number first
- Priority arg (e.g. `P2`) → restrict to that label only
- Issue number arg → skip this phase, go straight to Phase 1

The filtered, sorted list is your candidate set. Pick the first one → proceed to Phase 1.

---

## Phase 1 — Pre-flight

```bash
git status --porcelain
```

If alpha is dirty: **stop**. Ask whether to commit first or carry changes into the new branch.

Mark the issue in progress:
```bash
gh issue edit <number> --add-label "in progress"
```

---

## Phase 2 — Worktree Setup

Run from the repo root:
```bash
wtp add -b feature/<issue-number>-short-description
```

**If branch already exists:** check `git worktree list`. If no open worktree, add without `-b`. Run `git log --oneline -5` to surface prior commits — treat as partial implementation.

**Immediately verify the base:**
```bash
git -C worktrees/<branch> log --oneline -1
git log --oneline -1
```

If they don't match: delete the worktree and branch, redo from the repo root. Never proceed on the wrong base.

---

## Phase 3 — Formulate

Before writing any code, read the issue and relevant source to produce a tight implementation spec. This step is mandatory — it is what makes subagent dispatch reliable.

**Read in parallel:**
- Full issue body: `gh issue view <number> --json title,body,labels`
- DEV_LOG header: `head -100 DEV_LOG.md`
- Current CLI surface: `parallax --help && parallax schema`
- Relevant source files: grep for affected modules, then read them

**Write an implementation spec (keep in context, do not write to disk):**
```
Files to change:
  - src/parallax/foo.py:42 — <exactly what>
  - src/parallax/bar.py:10 — <exactly what>

Files NOT to touch:
  - <adjacent but out of scope>

Test that must pass:
  - uv run pytest tests/test_foo.py::test_name

Invariants to preserve:
  - <any non-obvious constraints from DEV_LOG or CLAUDE.md>

Assumptions to validate:
  - <what must be true> — validated by: <cheapest CLI/log/source check>
  (Run each check NOW, before writing any code.)
```

---

## Phase 4 — Implement

**Scope gate:**
- **≤ 3 files:** implement inline in the feature worktree. Write tests first, run `uv run pytest`.
- **> 3 files or multiple subsystems:** dispatch a Sonnet subagent.

### Subagent dispatch

Construct the subagent prompt with everything it needs — do not make it explore:
- The implementation spec (full text from Phase 3)
- Contents of every file it will touch (paste inline)
- Worktree path: `worktrees/<branch>/`
- Explicit rules: write tests first, run `uv run pytest`, stage changes but do NOT commit
- Report back: `DONE` | `DONE_WITH_CONCERNS <details>` | `NEEDS_CONTEXT <what>` | `BLOCKED <reason>`

**Handle subagent status:**
- `DONE`: review staged diff, run `uv run pytest`, check for new Pyright errors (`uv run pyright src/ 2>&1 | grep error` — zero new errors required), then commit.
- `DONE_WITH_CONCERNS`: read concerns first. Correctness/scope → address before committing.
- `NEEDS_CONTEXT`: provide missing context, redispatch with same model.
- `BLOCKED`: if context problem → provide more + redispatch. If plan wrong → escalate to user.

**Orchestrator owns the commit.** Subagent stages only. After verifying diff, `uv run pytest` is green, and `uv run pyright src/` shows no new errors:
```bash
git -C worktrees/<branch> add <files>
git -C worktrees/<branch> commit -m "<message>"
```

Open a PR targeting `alpha`:
```bash
gh pr create --base alpha --title "<title>" --body "..."
```

---

## Phase 4 — Install & Test

Run from inside the **feature worktree**:
```bash
just pr-install <pr-number>
```

This installs the CLI as `parallax-pr<N>` — an isolated command backed by a dedicated virtualenv at `~/.parallax-pr-<N>/`. The main `parallax` install is untouched.

Build the testing plan — every command copy-pasteable, no placeholders:

- Use `/tmp/parallax-pr<N>-test/` as test folder — include `mkdir` and plan.yaml write steps
- Set `PARALLAX_TEST_MODE=1`, `PARALLAX_LOG_DIR=/tmp/parallax-pr<N>-logs`, and `PARALLAX_USAGE_LOG=/tmp/parallax-pr<N>-usage.ndjson` inline on every produce command
- Cover every CLI path the change touches (new flags, subcommands, output formats, edge cases) — not just a smoke test

**Dispatch a Sonnet subagent to execute the full testing plan.** Give it:
- The installed command name: `parallax-pr<N>`
- Every test command to run (exact, copy-pasteable)
- Pass criteria and fail criteria for each
- Instruction to report back: exit codes, exact output observed, which criteria passed/failed

The subagent must run every command and return a structured report:
```
[TEST RESULTS] PR #<n> — <title>

Command: <exact command>
Exit: <code>  Output: <observed output>
Result: PASS | FAIL — <why>

... (one block per command)

Overall: PASS | FAIL
```

Present the full report to the user.

**Command formatting rule:** Any command you give the user to run must appear alone in its own code block — never inline inside prose. One command per block, nothing else on the line.

**If any path failed — retry loop (max 1 retry):**

1. **First failure:** Diagnose the root cause from the sub-agent output, make the minimal targeted fix on the feature branch, commit, re-run `just pr-install <pr-number>`, and re-dispatch the sub-agent with the same test plan.
2. **Second failure:** Stop. Surface the failure to the user with:
   - What command failed
   - Exact output / error observed
   - Why we believe it failed
   - Recommendation: close the PR, update the issue with learnings, re-open for a fresh attempt

   Run these steps after user confirms:
   ```bash
   gh pr close <number> --comment "Closing after two failed CLI test attempts. <what failed, why we think it failed>"
   gh issue edit <number> --remove-label "in progress" --add-label "ready"
   gh issue comment <number> --body "Attempted in PR #<n>. Failed because: <root cause>. Next attempt should: <concrete suggestion>."
   ```

   Do NOT attempt a third fix. The implementation approach needs rethinking.

**STOP. Do not proceed until the user confirms pass.**

If testing fails: fix on the feature branch, re-run `just pr-install`, re-surface the testing block. If the approach is fundamentally wrong: close PR, revert, comment on issue with what failed and why, re-label `ready`, start fresh.

**After user confirms pass — write DEV_LOG on the feature branch before merging:**
```bash
# Edit DEV_LOG.md in worktrees/<branch>/ — newest-first entry, tag + **Breaks if:** line
git -C worktrees/<branch> add DEV_LOG.md
git -C worktrees/<branch> commit -m "docs: DEV_LOG entry for PR #<n>"
git -C worktrees/<branch> push origin HEAD
```
The DEV_LOG commit gets squash-merged into alpha — no separate bookkeeping commit needed post-merge.

---

## Phase 5 — Merge & Cleanup

After DEV_LOG is committed on the feature branch. Run without stopping:

0. Sync alpha and rebase:
   ```bash
   git pull origin alpha
   git -C worktrees/<branch> rebase origin/alpha
   ```
   If conflicts: resolve, then `git -C worktrees/<branch> push --force-with-lease origin HEAD`. DEV_LOG.md conflicts are the most common: always resolve by placing the new entry ABOVE the conflicting HEAD block (DEV_LOG is newest-first). If rebase meaningfully changes the feature, re-surface the testing block before merging.

1. `gh pr merge <number> --squash` — never pass `--delete-branch`
2. `just pr-clean <pr-number>` — run from the repo root
3. `git pull origin alpha`
4. `wtp remove --force <branch>` then `git push origin --delete <branch>`
5. `just bump-and-install` — from the repo root
6. `git push origin alpha` — keeps local and remote alpha in sync; prevents divergence next cycle

---

## Phase 6 — Complete

```bash
gh issue close <number> --comment "Closed by PR #<pr> — verified on alpha <version>"
git status   # must be clean
```

Output:
```
- Merged: PR #<n> — <title>
- Closed: Issue #<n> — <title>
- Version: <version>
- DEV_LOG: updated

[COMPLETE]
```

Then run `/improve` to surface friction from this session.

---

## Rules

- Never branch from main — always from the repo root (alpha)
- Never skip base verification after `wtp add`
- Never merge before user confirms testing passed
- Never claim [COMPLETE] without user verification
- `just pr-install` runs from the **feature worktree**
- `just pr-clean` and `just bump-and-install` run from the **repo root**
- Never pass `--delete-branch` to `gh pr merge` — git refuses to delete a branch checked out by a worktree
- Alpha must be clean when the cycle ends
- Subagents stage only — orchestrator owns the commit and the PR
- Never dispatch a subagent without first producing the Phase 3 implementation spec
