---
name: git-pr
description: "Create and manage pull requests: generate descriptions, add labels, request reviewers, and ensure CI readiness."
source: community
allowed-tools: "*"
user-invocable: true
---

# Git PR Manager

Create, enhance, and manage pull requests with proper descriptions, labels, and review assignments.

## STEP 1: ANALYZE BRANCH

Examine the current branch:

- Compare against the base branch (usually main)
- List all commits in the branch
- Summarize the changes by file and type
- Check if CI/tests pass
- Verify the branch is pushed to remote

## STEP 2: CREATE PR

Generate a well-structured pull request:

### Title
- Under 70 characters
- Descriptive of the change (not the branch name)
- Use conventional format: `type: description`

### Description
```markdown
## Summary
Brief description of what this PR does and why.

## Changes
- Bullet list of specific changes

## Testing
- How to test these changes
- What edge cases to verify

## Screenshots
(if applicable)
```

### Metadata
- Labels (bug, feature, enhancement, documentation)
- Reviewers (based on code ownership or file history)
- Milestone or project (if applicable)
- Issue links (closes #123)

## STEP 3: PRE-SUBMIT CHECKLIST

Verify:
- [ ] All commits are clean and well-described
- [ ] No debug code or console.logs left behind
- [ ] Tests pass
- [ ] No unrelated changes included
- [ ] Branch is up to date with base
- [ ] Documentation updated if needed

## STEP 4: CREATE

Use `gh pr create` with the generated title and description.

## STEP 5: REPORT

Provide:
- PR URL
- Reviewers requested
- Any issues or warnings found during analysis
