import { afterEach, expect, test } from "bun:test"
import { spawn, type Subprocess } from "bun"
import { Client } from "@modelcontextprotocol/sdk/client/index.js"
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js"
import { fileURLToPath } from "node:url"

const cli = fileURLToPath(new URL("../src/main.ts", import.meta.url))
const bun = process.execPath
const backend = fileURLToPath(new URL("./mcp-backend.py", import.meta.url))
const root = fileURLToPath(new URL("../../", import.meta.url))
const processes: Subprocess[] = []

async function availablePort(): Promise<number> {
  const server = Bun.listen({
    hostname: "127.0.0.1",
    port: 0,
    socket: { data() {} },
  })
  const port = server.port
  server.stop()
  return port
}

async function backendUrl(): Promise<string> {
  const port = await availablePort()
  const url = `http://127.0.0.1:${port}`
  processes.push(
    spawn(["uv", "run", "python", backend], {
      cwd: root,
      env: { ...process.env, MCP_TEST_PORT: String(port) },
      stdout: "ignore",
      stderr: "inherit",
    })
  )
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      const response = await fetch(`${url}/openapi.json`)
      if (response.ok) return url
    } catch {}
    await Bun.sleep(100)
  }
  throw new Error("MCP test backend failed to start")
}

async function connect(url: string, user: string): Promise<Client> {
  const transport = new StdioClientTransport({
    command: bun,
    args: [cli, "mcp"],
    cwd: root,
    env: {
      ...process.env,
      OPEN_SWE_BACKEND_URL: url,
      OPEN_SWE_SESSION: user,
      OPEN_SWE_API_KEY: "",
      ACTIONS_ID_TOKEN_REQUEST_URL: "",
      ACTIONS_ID_TOKEN_REQUEST_TOKEN: "",
    },
    stderr: "inherit",
  })
  const client = new Client({ name: "mcp-e2e", version: "1" })
  await client.connect(transport)
  return client
}

afterEach(async () => {
  for (const process of processes.splice(0)) {
    process.kill()
    await process.exited
  }
})

test.skipIf(!Bun.which("uv"))(
  "stdio MCP catalog and invocation respect the backend session",
  async () => {
    const url = await backendUrl()
    const admin = await connect(url, "admin")
    const user = await connect(url, "user")
    try {
      const adminCatalog = await admin.listTools()
      const userCatalog = await user.listTools()
      expect(adminCatalog.tools.map((tool) => tool.name)).toContain(
        "manage_feature_flags"
      )
      expect(userCatalog.tools.map((tool) => tool.name)).not.toContain(
        "manage_feature_flags"
      )
      const result = await admin.callTool({
        name: "manage_feature_flags",
        arguments: { action: "read" },
      })
      expect(result.isError).toBeFalsy()
      expect(result.content).toMatchObject([
        { type: "text", text: expect.stringContaining('"scope": "instance"') },
      ])
      const denied = await user.callTool({
        name: "manage_feature_flags",
        arguments: { action: "read" },
      })
      expect(denied.isError).toBe(true)
    } finally {
      await Promise.all([admin.close(), user.close()])
    }
  },
  20000
)
