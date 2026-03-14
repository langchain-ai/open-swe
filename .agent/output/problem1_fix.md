# Problem 1 Fix: GitHub PR Review Comments Access from Slack

## Problem Description

When the open-swe agent is triggered from a Slack mention to work on a GitHub PR, it cannot read the PR review comments left by reviewers. This forces the agent to guess at what changes are needed, leading to multiple back-and-forth rounds before the correct fix is made.

## Root Cause

When triggered from GitHub PR comments, `webapp.py` pre-fetches PR comments via `fetch_pr_comments_since_last_tag()` and includes them in the initial prompt. However, when triggered from Slack, the `process_slack_mention()` function only includes Slack thread messages — no GitHub PR comments are included.

The agent had no way to fetch PR comments itself because:
1. The `http_request` tool would need auth headers but the agent does not know the GitHub token value
2. The `execute` tool cannot call `gh api` since no GitHub credentials are persistent in the sandbox
3. `task` subagents spawned by the agent also have no auth (confirmed returning 404 in production traces)

## Fix Implemented

A new tool `fetch_github_pr_comments` was created that:
- Reads the repo config from LangGraph's configurable context (same pattern as other tools)
- Resolves a GitHub token by first trying `get_github_token()` (reads from run metadata) and falling back to `get_github_app_installation_token()` (GitHub App credentials from env vars)
- Calls `fetch_pr_comments_since_last_tag()` from `agent/utils/github_comments.py` to retrieve all PR comments since the last @open-swe mention
- Returns both a structured list of comments and a pre-formatted human-readable string

### Files Modified

- **`agent/tools/fetch_github_pr_comments.py`** (new): The tool implementation
- **`agent/tools/__init__.py`**: Added import and export of `fetch_github_pr_comments`
- **`agent/server.py`**: Added import and registered the tool in `create_deep_agent`
- **`agent/prompt.py`**: Added `fetch_github_pr_comments` entry to `TOOL_USAGE_SECTION`

## PR URL

See the pull request at: https://github.com/langchain-ai/open-swe/pull/1059
