import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js"

import { ApiClient, ApiError } from "./api.ts"
import { readConfig } from "./config.ts"
import {
  listThreadsArgs,
  listThreadsResult,
  listThreadsResultFrom,
  threadsPageQuery,
} from "./threads.ts"

async function sessionClient(): Promise<ApiClient> {
  const config = await readConfig()
  if (config === null)
    throw new Error("not signed in — run `oswe login`, or set OPEN_SWE_SESSION")
  if (config.credential.machine)
    throw new Error(
      `${config.credential.source} cannot list threads; sign in as a person with \`oswe login\``
    )
  return new ApiClient(config.backend, config.credential)
}

export function createMcpServer(
  version: string,
  client: () => Promise<ApiClient> = sessionClient
): McpServer {
  const server = new McpServer({ name: "oswe", version })
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
        .catch((cause: unknown) => {
          if (cause instanceof ApiError && cause.status === 401)
            throw new Error(api.credential.rejected)
          throw cause
        })
      const result = listThreadsResultFrom(page, (id) =>
        api.dashboardUrl(`/agents/${encodeURIComponent(id)}`)
      )
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
