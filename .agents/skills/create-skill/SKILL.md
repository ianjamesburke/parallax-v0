---
name: create-skill
description: Create or revise an agent skill in this repo's .agents/skills/ folder. Use when the user says "make a skill", "codify this workflow", or a repeated procedure should be reusable. Not for one-off instructions.
---

# Create Skill

Skills live in `.agents/skills/<name>/SKILL.md` in the repo that owns the workflow. Tracked by git (`.gitignore` re-includes `.agents/skills/`).

## 1. Decide it is a skill
- Repeated procedure with steps and checks: yes.
- One fact or rule: put it in `CLAUDE.md` or `TRUTH.md`, not a skill.
- Product knowledge the CLI can expose: put it in the CLI (`--help`, `guide`), not a skill.
- Name the problem it solves in one line. No problem, no skill.

## 2. Check for overlap
- `find ~/.agents/skills ~/.claude/skills .agents/skills -maxdepth 1 -name "*<keyword>*"` (never Glob in `~/`).
- Existing skill covers it: edit that one.

## 3. Write
- Frontmatter: `name` (kebab-case, equals folder name), `description` (what it does plus when to trigger, one or two sentences).
- Body: numbered steps with exact commands. Each step has one check that proves it worked.
- Name every path, flag and output field the agent will read. No guesses.
- Put decisions the human owns in an explicit "Human decides" list. Everything else: the skill decides.
- Under ~150 lines. Fragments over sentences. No em dashes.
- No code the agent must not run blindly. No secrets, no API keys, no paid commands without a stated human gate.
- Cite the source for any fact about a tool or platform (URL or file path).

## 4. Wire and verify
- Repo skill: nothing to link; it is found under `.agents/skills/`.
- Global use across repos: `ln -s <abs repo path>/.agents/skills/<name> ~/.agents/skills/<name>`. Verify with `ls -la ~/.agents/skills/<name>`.
- Dry-run the steps once on a scratch case. Fix any step that does not work as written.
- Commit the skill directly to the integration branch (meta files, no PR), with trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` or the current model.

## 5. Report
- Path, what it covers, the one dry-run result. End with `RECOMMENDATION:` and one call.
