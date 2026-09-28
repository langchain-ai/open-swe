import { afterEach, describe, expect, test } from "bun:test"
import { Client } from "@modelcontextprotocol/sdk/client/index.js"
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js"

import { ApiClient } from "../src/api.ts"
import { SessionCredential } from "../src/credentials.ts"
import { createMcpServer } from "../src/mcp.ts"

const stops: (() => Promise<void>)[] = []

afterEach(async () => {
  await Promise.all(stops.splice(0).map((stop) => stop()))
})

async function connect(
  respond: (url: URL) => Response
): Promise<{ client: Client; queries: URLSearchParams[] }> {
  const queries: URLSearchParams[] = []
  const backend = Bun.serve({
    hostname: "127.0.0.1",
    port: 0,
    fetch(request) {
      const url = new URL(request.url)
      queries.push(url.searchParams)
      return respond(url)
    },
  })
  const api = new ApiClient(
    `http://127.0.0.1:${backend.port}`,
    new SessionCredential("jwt", "session (test)")
  )
  const server = createMcpServer("0.0.0-test", async () => api)
  const client = new Client({ name: "test", version: "0" })
  const [clientSide, serverSide] = InMemoryTransport.createLinkedPair()
  await Promise.all([server.connect(serverSide), client.connect(clientSide)])
  stops.push(async () => {
    await client.close()
    await server.close()
    await backend.stop(true)
  })
  return { client, queries }
}

describe("list_threads", () => {
  test("defaults to the sidebar's view and projects each thread", async () => {
    const { client, queries } = await connect(() =>
      Response.json({
        items: [
          {
            id: "t-1",
            title: "Fix login",
            status: "running",
            repoFullName: "acme/web",
            branch: "fix-login",
            viewed: false,
            resolved: false,
            createdAt: 0,
            updatedAt: 1_000,
            pr: {
              number: 7,
              title: "Fix login",
              state: "open",
              url: "https://github.com/acme/web/pull/7",
            },
          },
        ],
        offset: 0,
        limit: 25,
        hasMore: true,
      })
    )

    const result = await client.callTool({
      name: "list_threads",
      arguments: {},
    })

    expect(Object.fromEntries(queries[0] ?? [])).toEqual({
      limit: "25",
      offset: "0",
      scope: "interactive",
      sort_by: "created_at",
      resolved: "false",
    })
    expect(result.isError).toBeFalsy()
    expect(result.structuredContent).toMatchObject({
      has_more: true,
      next_offset: 1,
      threads: [
        {
          id: "t-1",
          status: "running",
          unread: true,
          archived: false,
          repo: "acme/web",
          created_at: "1970-01-01T00:00:00.000Z",
          url: expect.stringMatching(/\/agents\/t-1$/),
          pull_request: { number: 7 },
        },
      ],
    })
  })

  test("maps filters onto the page query", async () => {
    const { client, queries } = await connect(() =>
      Response.json({ items: [], offset: 10, hasMore: false })
    )

    await client.callTool({
      name: "list_threads",
      arguments: {
        no_repo: true,
        include_archived: true,
        include_automations: true,
        sort: "updated",
        unread: true,
        status: "error",
        query: "login",
        offset: 10,
      },
    })

    expect(Object.fromEntries(queries[0] ?? [])).toEqual({
      limit: "25",
      offset: "10",
      scope: "all",
      sort_by: "updated_at",
      ownerless: "true",
      status: "error",
      viewed: "false",
      q: "login",
    })
  })

  test("reports an expired session as a tool error", async () => {
    const { client } = await connect(
      () => new Response(JSON.stringify({ detail: "expired" }), { status: 401 })
    )

    const result = await client.callTool({
      name: "list_threads",
      arguments: {},
    })

    expect(result.isError).toBe(true)
    expect(JSON.stringify(result.content)).toContain("oswe login")
  })
})
