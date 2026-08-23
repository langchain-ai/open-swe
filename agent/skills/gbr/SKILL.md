---
name: gbr
description: >
  Pair a phone running Build Remote Agent to this Open SWE desktop session.
  Requires gbr-agent run on the host. Attach via Bot API 127.0.0.1:8788 or gbr-mcp.
  Use when the user wants mobile spectator / inject while Open SWE (dashboard, Slack, or Linear) is running.
compatibility: Requires gbr-agent ≥ 0.6.0 on the host. Loopback only. No mailbox keys in this file.
metadata:
  version: "0.6.1"
  product: "Build Remote Agent"
  website: "https://grokbuildremote.com/"
---

# Build Remote Agent — pairing device

One adapter for Open SWE. Protocol `gbr/1`. No fourth pair protocol.

Independent product by Linespotting AB. Not affiliated with xAI or SpaceX.

Open SWE keeps orchestration (LangGraph agent, reviewer, analyzer, sandboxes).
The phone spectates and can veto or inject; it does not orchestrate.
Load this skill on the **host** process. Do not put mailbox keys in the sandbox.

## Pair (unchanged)

1. Phone: [Build Remote Agent](https://grokbuildremote.com/) → Connect.
2. PC: `gbr-agent pair` — browser QR **and** printed 8-char code.
3. Phone scans QR **or** types the 8-char code.
4. PC: `gbr-agent run` (keep it running beside `langgraph dev` / the dashboard).

```bash
curl -fsSL https://grokbuildremote.com/install.sh | bash   # Windows: irm https://grokbuildremote.com/install.ps1 | iex
gbr-agent version    # need v0.6.0+
gbr-agent pair && gbr-agent run
```

Unpair on the phone before a new mailbox. Force-close is not enough.

## Attach (only these)

| How | Where |
|-----|--------|
| Bot API | `http://127.0.0.1:8788` after `gbr-agent run` |
| MCP | `gbr-mcp` stdio (same JSON as Bot API) |

Phone is spectator + veto, not orchestrator. The Open SWE dashboard (`:2024`) and Slack/Linear threads stay the product UI.

```bash
curl -sS http://127.0.0.1:8788/health
curl -sS http://127.0.0.1:8788/v1/sessions
curl -sS -X POST http://127.0.0.1:8788/v1/inject \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"SESSION","text":"hello","submit":true}'
```

## MCP

Optional host-side stdio server (same pattern as other Open SWE MCP integrations: credentials stay on the LangGraph process, never in the sandbox):

```bash
git clone https://github.com/LinespottingOrg/GrokBuildRemote-Agents.git
cd GrokBuildRemote-Agents/mcp/gbr-mcp && npm install
node bin/gbr-mcp.js --diagnose
```

Never put mailbox keys, `X-GBR-Key`, or `~/.gbr/` in this skill or in git.

## Loop

diagnose → open/attach → lock → inject → wait idle → harvest excerpt → iterate or close

Docs: https://github.com/LinespottingOrg/GrokBuildRemote-Agents/blob/main/docs/BOT-API.md
