import { Client } from "@modelcontextprotocol/sdk/client/index.js"
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js"
import { parseArgs } from "node:util"

import { ApiClient } from "./api.ts"
import { readPipedStdin } from "./input.ts"
import { isRecord } from "./json.ts"
import { createMcpServer } from "./mcp.ts"

export async function toolCommand(
  argv: readonly string[],
  version: string,
  apiClient?: () => Promise<ApiClient>
): Promise<number> {
  const { values, positionals } = parseArgs({
    args: [...argv],
    allowPositionals: true,
    options: {
      json: { type: "string" },
      help: { type: "boolean", short: "h" },
    },
  })
  const command = positionals[0]
  if (!command || positionals.length !== 1)
    throw new Error("usage: oswe tool NAME [--json <object>] [--help]")
  const server = await createMcpServer(version, apiClient)
  const client = new Client({ name: "oswe-cli", version })
  const [clientSide, serverSide] = InMemoryTransport.createLinkedPair()
  try {
    await Promise.all([server.connect(serverSide), client.connect(clientSide)])
    const tools = []
    let cursor: string | undefined
    do {
      const page = await client.listTools(cursor ? { cursor } : {})
      tools.push(...page.tools)
      cursor = page.nextCursor
    } while (cursor)
    if (command === "tools") {
      process.stdout.write(`${JSON.stringify(tools, null, 2)}\n`)
      return 0
    }
    const name = command.replaceAll("-", "_")
    const tool = tools.find((candidate) => candidate.name === name)
    if (!tool) throw new Error(`unknown command ${command}; run oswe tools`)
    if (values.help) {
      process.stdout.write(`${JSON.stringify(tool, null, 2)}\n`)
      return 0
    }
    const input = values.json ?? (await readPipedStdin())
    const args: unknown = input.trim() ? JSON.parse(input) : {}
    if (!isRecord(args)) throw new Error("tool arguments must be a JSON object")
    const result = await client.callTool({ name, arguments: args })
    const content = result.content
    const output = result.isError ? process.stderr : process.stdout
    if (result.structuredContent !== undefined) {
      output.write(`${JSON.stringify(result.structuredContent, null, 2)}\n`)
    } else if (Array.isArray(content)) {
      for (const item of content) {
        if (
          isRecord(item) &&
          item.type === "text" &&
          typeof item.text === "string"
        )
          output.write(`${item.text}\n`)
      }
    }
    return result.isError ? 1 : 0
  } finally {
    await client.close()
    await server.close()
  }
}
