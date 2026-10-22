# Mirroring Claude Code sessions

Claude Code can post its [hook events](https://code.claude.com/docs/en/hooks) to a URL. Open SWE uses that to mirror a person's Claude Code sessions into the dashboard: each session becomes a private, read-only thread that updates while the session runs.

## Connect a machine

1. In the dashboard, open **Settings → Claude Code**, name the machine, and choose **Connect Claude Code**.
2. Merge the settings it shows into `~/.claude/settings.json`. They hold the machine's token, which is not shown again.
3. Start a new Claude Code session. It appears in the sidebar with a terminal icon on its first prompt.

Revoking a machine under **Machines** on the same page stops that machine from mirroring. Threads it already mirrored stay.

The settings register one [HTTP hook](https://code.claude.com/docs/en/hooks#http-hook-fields) per event below, all posting to `POST /dashboard/api/claude-code/hooks` with the token as a bearer token. If your organization limits hook targets with `allowedHttpHookUrls`, allow that URL.

## What a thread shows

| Hook event | In the thread |
|---|---|
| `UserPromptSubmit` | A new turn with the prompt. A prompt that arrives while the previous turn is still open ends that turn as interrupted, since Claude Code fires no event when you press Esc. |
| `PreToolUse` | A tool call, shown as running. |
| `PostToolUse`, `PostToolUseFailure` | The tool call's result or error. |
| `Stop` | Claude's final reply, and the turn completes. |
| `StopFailure` | The turn fails with the API error. |
| `SessionEnd` | An open turn ends as interrupted. |

Hooks only see what Claude Code hands them, so the text Claude writes between tool calls does not appear; the reply that ends a turn does. Steps inside a subagent appear only as its tool call.

A thread also picks up its pull request, records it like one the agent opened, and links the two, when the session:

- binds a PR in the Claude app (`mcp__ccd_pr__bind_pr`),
- reads its own bound PR (`mcp__ccd_pr__get_status`), or
- opens one with `gh pr create`.

The PR is read as you, so a repository you cannot see is never linked.

## Guarantees

- **Read-only.** Nobody can prompt, steer or stop a mirrored thread from Open SWE, including you. Continue it in Claude Code.
- **Private.** Only you and workspace admins can open it: a local session can contain anything on your machine.
- **Never in the way.** The endpoint answers with an empty body, so Claude Code never reads it as a decision, and the hooks time out after 5 seconds. A failed hook is logged by Claude Code and the session continues.
- **Yours only.** A thread's id is derived from your user and Claude Code's session id, so another person's token cannot write into it whatever session id it sends.

## Try it locally

Run the backend, Managed Deep Agents and the dashboard as in [Local Development](../DEVELOPMENT.md#6-run), sign in, and connect a machine from **Settings → Claude Code**. The generated settings point at your `DASHBOARD_API_BASE_URL`, which is `http://localhost:2024` by default. To try them without changing your own Claude Code settings, save them to a file and pass it for one session:

```bash
claude --settings ./open-swe-hooks.json
```
