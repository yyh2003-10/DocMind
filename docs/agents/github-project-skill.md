# GitHub Project Management Skill - Design Document

> **Purpose**: A comprehensive AI skill/tool for managing GitHub projects, designed for integration into ZCode/AtomCode workflows.
> **Last Updated**: 2026-08-19
> **Status**: Design Document

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture & Integration](#3-architecture--integration-model)
3. [Issue Management](#5-issue-management)
4. [Pull Request Management](#6-pull-request-management)
5. [Release Management](#7-release-management)
6. [Project Board Management](#8-project-board-management)
7. [Repository Management](#9-repository-management)
8. [Label Management](#10-label-management)
9. [Skill SKILL.md Template](#12-skill-skillmd-template)
10. [Reference Sources](#13-reference-sources)

---

## 1. Overview

This skill provides a complete AI-driven interface for managing every aspect of GitHub repositories through the **GitHub CLI (`gh`)**.

| Domain | Capabilities |
|--------|-------------|
| **Issues** | Create, list, view, edit, close, reopen, assign, label, comment, transfer, pin, lock |
| **Pull Requests** | Create, list, view, edit, review, merge, close, revert, checkout |
| **Releases** | Create, list, view, edit, upload assets, download assets, delete |
| **Projects (v2)** | Create, list, view, edit, delete, manage fields, add/edit items |
| **Repositories** | Create, list, clone, delete, fork, rename, edit settings |
| **Labels** | Create, list, edit, delete, clone between repos |

---

## 5. Issue Management

### 5.1 Create Issue
```bash
gh issue create --title "Bug: ..." --body "..." --label "bug" --assignee "@me" -R OWNER/REPO
```
### 5.2 List Issues
```bash
gh issue list --state all --label "bug" --json number,title,state,assignees,labels
```
### 5.3 View Issue
```bash
gh issue view 123 --comments --json number,title,body,state,assignees,labels
```
### 5.4 Edit Issue
```bash
gh issue edit 123 --add-label "bug" --add-assignee "@me" --add-project "Roadmap"
```
### 5.5 Close / Reopen
```bash
gh issue close 123
gh issue reopen 123
```
### 5.6 Comment
```bash
gh issue comment 123 --body "This has been fixed."
```

---

## 6. Pull Request Management

### 6.1 Create PR
```bash
gh pr create --fill --base main --label "feature" --reviewer "username" -R OWNER/REPO
```
### 6.2 List PRs
```bash
gh pr list --state all --author "@me" --json number,title,state,isDraft,reviewDecision
```
### 6.3 View PR
```bash
gh pr view 456 --comments --json number,title,state,mergeStateStatus,reviewDecision
```
### 6.4 Review PR
```bash
gh pr review 456 --approve --body "LGTM"
gh pr review 456 --request-changes --body "Please fix"
gh pr review 456 --comment --body "Question about line 42"
```
### 6.5 Merge PR
```bash
gh pr merge 456 --squash --delete-branch
gh pr merge 456 --rebase
gh pr merge 456 --merge
```
### 6.6 Close / Revert
```bash
gh pr close 456
gh pr revert 456
```

---

## 7. Release Management

### 7.1 Create Release
```bash
gh release create v1.2.3 --generate-notes --title "Version 1.2.3" -R OWNER/REPO
gh release create v1.2.3 --draft --generate-notes
gh release create v1.2.3 --prerelease
```
### 7.2 Upload Assets
```bash
gh release upload v1.2.3 ./dist/*.tgz --clobber
```
### 7.3 Download Assets
```bash
gh release download v1.2.3 --pattern '*.zip' --dir ./releases
```
### 7.4 List / View
```bash
gh release list --json tagName,name,isDraft,isPrerelease,isLatest
gh release view v1.2.3 --json tagName,name,body,assets
```
### 7.5 Edit Release
```bash
gh release edit v1.0 --draft=false --latest
```
### 7.6 Delete Release
```bash
gh release delete v1.2.3 --cleanup-tag --yes
```

---

## 8. Project Board Management

### 8.1 Create Project
```bash
gh project create --owner "@me" --title "Q3 Roadmap"
```
### 8.2 List / View
```bash
gh project list --owner "@me"
gh project view 1 --owner "@me"
```
### 8.3 Create Field
```bash
gh project field-create 1 --owner "@me" --name "Status" --data-type SINGLE_SELECT --single-select-options "Todo,In Progress,Review,Done"
```
### 8.4 Add Item
```bash
gh project item-add 1 --owner "@me" --url "https://github.com/OWNER/REPO/issues/123"
```
### 8.5 Edit / Delete Project
```bash
gh project edit 1 --title "Updated Title"
gh project delete 1 --owner "@me"
```

---

## 9. Repository Management

### 9.1 Create Repo
```bash
gh repo create my-project --public --clone --description "..." --add-readme --gitignore Python
```
### 9.2 Edit Repo
```bash
gh repo edit OWNER/REPO --description "..." --enable-squash-merge --delete-branch-on-merge
```
### 9.3 Other
```bash
gh repo fork OWNER/REPO
gh repo archive OWNER/REPO
gh repo delete OWNER/REPO
```

---

## 10. Label Management

### 10.1 Create Labels
```bash
gh label create "priority:critical" --color "B60205" --description "Must be fixed immediately"
gh label create "type:bug" --color "E99695" --description "Something is broken"
gh label create "type:feature" --color "A2EEEF" --description "New feature"
gh label create "status:needs-triage" --color "EB6420"
gh label create "status:in-progress" --color "0075CA"
gh label create "status:blocked" --color "F4D03F"
gh label create "area:core" --color "D7BDE2"
gh label create "area:ui" --color "C5DEF5"
```

### 10.2 Recommended Label Scheme

**Priority**: `priority:critical`, `priority:high`, `priority:medium`, `priority:low`
**Type**: `type:bug`, `type:feature`, `type:enhancement`, `type:docs`, `type:chore`
**Status**: `status:needs-triage`, `status:in-progress`, `status:blocked`, `status:needs-review`, `status:done`
**Area**: `area:core`, `area:ui`, `area:api`, `area:tests`, `area:infra`

---

## 12. Skill SKILL.md Template

```yaml
---
name: github-project-manager
description: "Manage GitHub projects end-to-end: issues, pull requests, releases, project boards, and repositories. Use when the user wants to create, update, close, merge, release, or organize anything on GitHub. Integrates with the gh CLI for all operations."
---
```

### Workflow Templates

#### Template A: Complete Issue Lifecycle
```bash
ISSUE_URL=$(gh issue create --title "$TITLE" --body-file "$BODY" --label "type:bug" --assignee "@me" -R OWNER/REPO)
ISSUE_NUM=$(echo "$ISSUE_URL" | grep -o '[0-9]*$')
gh issue edit "$ISSUE_NUM" --add-assignee "reviewer" -R OWNER/REPO
gh issue edit "$ISSUE_NUM" --add-project "Backlog" -R OWNER/REPO
gh issue close "$ISSUE_NUM" -R OWNER/REPO
```

#### Template B: PR Pipeline
```bash
PR_URL=$(gh pr create --fill --base main --label "type:feature" -R OWNER/REPO)
PR_NUM=$(echo "$PR_URL" | grep -o '[0-9]*$')
gh pr edit "$PR_NUM" --add-reviewer "reviewer" -R OWNER/REPO
gh pr merge "$PR_NUM" --squash --delete-branch -R OWNER/REPO
```

#### Template C: Semantic Release
```bash
VERSION="v$(date +%Y.%m.%d)-$(git rev-parse --short HEAD)"
gh release create "$VERSION" --generate-notes --target main -R OWNER/REPO
gh release upload "$VERSION" ./dist/* -R OWNER/REPO
```

---

## 13. Reference Sources

| Source | URL |
|--------|-----|
| GitHub CLI Manual | <https://cli.github.com/manual/gh> |
| GitHub Issues REST API | <https://docs.github.com/en/rest/issues/issues> |
| GitHub CLI Official Manual | <https://cli.github.com/manual> |

---

*End of Document*