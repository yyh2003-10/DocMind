---
name: github-project-manager
description: "Manage GitHub projects end-to-end: issues, pull requests, releases, project boards, and repositories. Use when the user wants to create, update, close, merge, release, or organize anything on GitHub. Integrates with the gh CLI for all operations."
---

# GitHub Project Manager Skill

## Trigger Conditions

Invoke this skill when the user requests any of:
- "create an issue", "file a bug", "open a ticket", "管理 issue"
- "create a PR", "open a pull request", "submit changes", "提 PR"
- "merge PR", "review PR", "approve PR", "合并 PR"
- "create a release", "tag a release", "deploy a version", "发布版本"
- "upload assets", "download release", "上传安装包"
- "manage project board", "add to project", "看板管理"
- "create a repo", "fork repo", "archive repo", "仓库管理"
- "manage labels", "add label", "assign issue", "标签管理"
- "close issue", "reopen issue", "transfer issue", "关闭 issue"
- "reorganize", "整理项目", "项目管理"

## Operating Instructions

### Step 1: Identify the operation domain
- Issue? Pull Request? Release? Project? Repository? Label?

### Step 2: Select the `gh` subcommand
- Use the command reference tables in `docs/agents/github-project-skill.md`.

### Step 3: Build the command
- Include the `-R OWNER/REPO` flag unless the default repo is already configured.
- Use `--json` for programmatic output when the agent needs to parse results.
- Use `--body-file` for long text bodies.

### Step 4: Execute and handle results
- Check exit code (0 = success, non-zero = error).
- On error, read stderr for the specific failure reason.
- For authentication errors, suggest `gh auth status` and `gh auth login`.

### Step 5: Report the outcome
- Always include the URL of the created/modified resource.
- For merges, report the merge commit SHA.
- For releases, report the download URL of assets.

## Safety Rules

1. **Never merge or delete without confirmation** unless explicitly instructed.
2. **Always verify PR state** before merging (check `--json mergeStateStatus,mergeable`).
3. **Use `--dry-run`** for PR creation when in uncertain mode.
4. **Confirm destructive operations** (delete release, delete repo) by requiring `--yes`.
5. **Check for existing tags** before creating releases (`--verify-tag`).
6. **Use `--cleanup-tag`** only when the user explicitly wants tag deletion.
7. **Always include `-R OWNER/REPO`** to avoid operating on the wrong repository.

## Quick Reference

```
ISSUES:  gh issue create --title T --body B --label L --assignee A -R O/R
PRs:     gh pr create --fill --base main --label L --reviewer R -R O/R
         gh pr merge N --squash|--rebase --delete-branch
RELEASE: gh release create TAG --generate-notes -R O/R
         gh release upload TAG ./dist/*
PROJECT: gh project create --owner @me --title T
REPO:    gh repo create NAME --public --clone
LABELS:  gh label create "type:bug" --color "E99695" --description "Bug"