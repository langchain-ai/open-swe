import { expect, test } from "bun:test"

import { ApiClient } from "../src/api.ts"
import { toolCommand } from "../src/commands.ts"
import { SessionCredential } from "../src/credentials.ts"

test("subcommands invoke backend tools and propagate tool errors", async () => {
  const calls: unknown[] = []
  const backend = Bun.serve({
    hostname: "127.0.0.1",
    port: 0,
    async fetch(request) {
      if (new URL(request.url).pathname.endsWith("/tools"))
        return Response.json([
          {
            name: "test_action",
            description: "Perform an action",
            parameters: {
              type: "object",
              properties: { count: { type: "integer" } },
              required: ["count"],
            },
            access: "session",
          },
        ])
      calls.push(await request.json())
      return Response.json({ error: "action refused" }, { status: 403 })
    },
  })
  const api = new ApiClient(
    `http://127.0.0.1:${backend.port}`,
    new SessionCredential("jwt", "session (test)")
  )
  try {
    expect(
      await toolCommand(
        ["test-action", "--json", '{"count":3}'],
        "0",
        async () => api
      )
    ).toBe(1)
    expect(calls).toHaveLength(1)
    await expect(
      toolCommand(["test_action", "--json", "[]"], "0", async () => api)
    ).rejects.toThrow("JSON object")
    expect(calls).toHaveLength(1)
  } finally {
    await backend.stop(true)
  }
})
