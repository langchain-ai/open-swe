import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js"
import { mcpInputSchema } from "./mcp-catalog.ts"
import { isRecord } from "./json.ts"
import { createSessionArgs, createSessionResult } from "./session.ts"

import { ApiClient, ApiError } from "./api.ts"
import { readConfig } from "./config.ts"
import {
  dismissHumanReviewRequestArgs,
  dismissHumanReviewRequestResult,
  requestHumanReviewArgs,
  requestHumanReviewResult,
} from "./review.ts"
import {
  listThreadsArgs,
  listThreadsResult,
  listThreadsResultFrom,
  threadsPageQuery,
} from "./threads.ts"
import {
  sessionUpload,
  uploadSessionArgs,
  uploadSessionResult,
} from "./upload.ts"

async function sessionClient(): Promise<ApiClient> {
  const config = await readConfig()
  if (config === null)
    throw new Error("not signed in — run `oswe login`, or set OPEN_SWE_SESSION")
  if (config.credential.machine)
    throw new Error(
      `${config.credential.source} cannot act for a person; sign in with \`oswe login\``
    )
  return new ApiClient(config.backend, config.credential)
}

function rejectedSession(api: ApiClient): (cause: unknown) => never {
  return (cause) => {
    if (cause instanceof ApiError && cause.status === 401)
      throw new Error(api.credential.rejected)
    throw cause
  }
}

export async function createMcpServer(
  version: string,
  client: () => Promise<ApiClient> = sessionClient
): Promise<McpServer> {
  const server = new McpServer({ name: "oswe", version })
  server.registerTool(
    "list_threads",
    {
      title: "List Open SWE threads",
      description:
        "List your Open SWE agent threads, newest first, filtered the way the web app sidebar filters them. By default archived threads and automation runs are left out.",
      inputSchema: listThreadsArgs,
      outputSchema: listThreadsResult,
      annotations: { readOnlyHint: true, openWorldHint: true },
    },
    async (args) => {
      const api = await client()
      const page = await api
        .listThreadsPage(threadsPageQuery(args))
        .catch(rejectedSession(api))
      const result = listThreadsResultFrom(page, (id) =>
        api.webUrl(`/agents/${encodeURIComponent(id)}`)
      )
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        structuredContent: result,
      }
    }
  )
  server.registerTool(
    "create_session",
    {
      title: "Create an Open SWE session",
      description:
        "Create a fresh Open SWE session from a prompt, without uploading a local transcript. Starts the agent immediately by default; set start=false to create an idle session. Uses the signed-in person's web defaults and repository access.",
      inputSchema: createSessionArgs,
      outputSchema: createSessionResult,
      annotations: { readOnlyHint: false, openWorldHint: true },
    },
    async (args) => {
      const api = await client()
      const threadId = await api.createSession(args).catch(rejectedSession(api))
      const result = {
        thread_id: threadId,
        url: api.webUrl(`/agents/${encodeURIComponent(threadId)}`),
      }
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        structuredContent: result,
      }
    }
  )
  server.registerTool(
    "upload_session",
    {
      title: "Upload this session to Open SWE",
      description:
        "Move this local coding session to Open SWE: its transcript becomes a new thread you continue from the web app, where a cloud agent picks up the work. No agent run starts until you send a message there. Before calling, commit every change in the working directory, including untracked files, and push it to `branch` on `repo`, or to the head branch of the pull request at `pr_url`; the cloud agent sees only what was pushed.",
      inputSchema: uploadSessionArgs,
      outputSchema: uploadSessionResult,
      annotations: { readOnlyHint: false, openWorldHint: true },
    },
    async (args) => {
      const upload = await sessionUpload(args)
      const api = await client()
      const threadId = await api
        .uploadSession(upload)
        .catch(rejectedSession(api))
      const result = {
        thread_id: threadId,
        url: api.webUrl(`/agents/${encodeURIComponent(threadId)}`),
      }
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        structuredContent: result,
      }
    }
  )
  server.registerTool(
    "request_human_review",
    {
      title: "Request a human review in Slack",
      description:
        "Ask people in Slack to review an open GitHub pull request: posts a card with its title, your inline_summary, and an \"I'll review\" button in the repository's review channel. It merges on its own once every reviewer who signs up approves on GitHub, or two hours after the request with at least one approval, when checks pass. Refused while the pull request is a draft (if you authored it, run `gh pr ready` first), has merge conflicts, or fails a required check, and unless the person signed in turned on human review requests on the web app's Feature Flags page. Asking again for a pull request you already asked about replaces the open card's summary. The card is the announcement: never link to it, since Slack unfurls the link into a second copy.",
      inputSchema: requestHumanReviewArgs,
      outputSchema: requestHumanReviewResult,
      annotations: { readOnlyHint: false, openWorldHint: true },
    },
    async ({ pr_url, inline_summary, channel }) => {
      const api = await client()
      const result = await api
        .requestHumanReview(pr_url, {
          inline_summary,
          ...(channel ? { channel } : {}),
        })
        .catch(rejectedSession(api))
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        structuredContent: result,
      }
    }
  )
  server.registerTool(
    "dismiss_human_review_request",
    {
      title: "Dismiss a human review request",
      description:
        "Take down a pull request's open human review request in Slack, exactly as its card's Dismiss button does: the card is marked dismissed by you, and the pull request no longer merges on its own. It does not close the pull request.",
      inputSchema: dismissHumanReviewRequestArgs,
      outputSchema: dismissHumanReviewRequestResult,
      annotations: { readOnlyHint: false, openWorldHint: true },
    },
    async ({ pr_url, reason }) => {
      const api = await client()
      const result = await api
        .dismissHumanReviewRequest(pr_url, reason ? { reason } : {})
        .catch(rejectedSession(api))
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        structuredContent: result,
      }
    }
  )
  const api = await client()
  const tools = await api.mcpTools().catch(rejectedSession(api))
  for (const tool of tools) {
    let inputSchema
    try {
      inputSchema = mcpInputSchema(tool)
    } catch (cause) {
      process.stderr.write(
        `oswe mcp: skipping ${tool.name}: ${String(cause)}\n`
      )
      continue
    }
    server.registerTool(
      tool.name,
      {
        description: tool.description,
        inputSchema,
        annotations: { openWorldHint: true },
      },
      async (args) => {
        const current = await client()
        if (!isRecord(args))
          throw new Error(`Invalid arguments for ${tool.name}`)
        const result = await current
          .mcpInvoke(tool.name, args)
          .catch(rejectedSession(current))
        return {
          content: [
            { type: "text" as const, text: JSON.stringify(result, null, 2) },
          ],
        }
      }
    )
  }
  return server
}

/** Serve MCP over stdio until the client closes stdin; stdout carries only protocol messages. */
export async function serveMcp(version: string): Promise<void> {
  await (await createMcpServer(version)).connect(new StdioServerTransport())
}
