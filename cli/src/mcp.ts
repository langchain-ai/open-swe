import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js"
import { exposedTools } from "./mcp-tools.ts"

import { ApiClient, ApiError } from "./api.ts"
import { readConfig } from "./config.ts"
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

export function createMcpServer(
  version: string,
  client: () => Promise<ApiClient> = sessionClient
): McpServer {
  const server = new McpServer({ name: "oswe", version })
  for (const tool of exposedTools) {
    server.registerTool(
      tool.name,
      {
        title: tool.title,
        description: tool.description,
        inputSchema: tool.inputSchema,
        annotations: { readOnlyHint: tool.readOnly, openWorldHint: true },
      },
      async (args) => {
        const api = await client()
        if (tool.access === "admin") {
          const identity = await api.me().catch(rejectedSession(api))
          if (identity.is_admin !== true)
            throw new Error("Only workspace admins can use this tool")
        }
        const result = await tool.run(api, args).catch(rejectedSession(api))
        return {
          content: [
            { type: "text" as const, text: JSON.stringify(result, null, 2) },
          ],
        }
      }
    )
  }
  server.registerTool(
    "list_threads",
    {
      title: "List Open SWE threads",
      description:
        "List your Open SWE agent threads, newest first, filtered the way the dashboard sidebar filters them. By default archived threads and automation runs are left out.",
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
        api.dashboardUrl(`/agents/${encodeURIComponent(id)}`)
      )
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
        "Move this local coding session to Open SWE: its transcript becomes a new thread you continue from the dashboard, where a cloud agent picks up the work. No agent run starts until you send a message there. Before calling, commit every change in the working directory, including untracked files, and push it to `branch` on `repo`, or to the head branch of the pull request at `pr_url`; the cloud agent sees only what was pushed.",
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
        url: api.dashboardUrl(`/agents/${encodeURIComponent(threadId)}`),
      }
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        structuredContent: result,
      }
    }
  )
  return server
}

/** Serve MCP over stdio until the client closes stdin; stdout carries only protocol messages. */
export async function serveMcp(version: string): Promise<void> {
  await createMcpServer(version).connect(new StdioServerTransport())
}
