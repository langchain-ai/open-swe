# Default Prompt

This file contains custom instructions that are injected into the agent's system prompt for every task. Edit this file to add org-level conventions, default behaviors, or repository guidance that should apply across all agent runs.

These instructions are loaded at agent startup and inserted into the system prompt before the repository setup section. For repo-specific instructions, use `AGENTS.md` in the repository root instead.

## Default Repository

When no repository is specified in the task (via `repo:owner/name` syntax, Linear team mapping, or GitHub context), you should work on the **langchainplus** repository under the **langchain-ai** GitHub organization.

- Default organization: `langchain-ai`
- Default repository: `langchainplus`

If the task does not mention a specific repository, use `list_repos(organization_name="langchain-ai")` and look for `langchainplus`.

## Organization Conventions

- All pull requests should follow the conventional commit format: `feat:`, `fix:`, `chore:`, `ci:`
- Tag the requesting user in Linear/Slack/GitHub comments when work is complete
- Follow existing code style and patterns in the target repository
