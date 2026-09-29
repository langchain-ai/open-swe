import { afterEach, describe, expect, test } from "bun:test"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { gunzipSync } from "node:zlib"
import { Client } from "@modelcontextprotocol/sdk/client/index.js"
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js"

import { ApiClient } from "../src/api.ts"
import { SessionCredential } from "../src/credentials.ts"
import { createMcpServer } from "../src/mcp.ts"

const stops: (() => Promise<void>)[] = []

afterEach(async () => {
  await Promise.all(stops.splice(0).map((stop) => stop()))
})

async function connect(respond: (url: URL) => Response): Promise<{
  client: Client
  queries: URLSearchParams[]
  bodies: string[]
  encodings: (string | null)[]
}> {
  const queries: URLSearchParams[] = []
  const bodies: string[] = []
  const encodings: (string | null)[] = []
  const backend = Bun.serve({
    hostname: "127.0.0.1",
    port: 0,
    async fetch(request) {
      const url = new URL(request.url)
      queries.push(url.searchParams)
      const encoding = request.headers.get("content-encoding")
      encodings.push(encoding)
      const raw = Buffer.from(await request.arrayBuffer())
      bodies.push(
        (encoding === "gzip" ? gunzipSync(raw) : raw).toString("utf8")
      )
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
  return { client, queries, bodies, encodings }
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

describe("exposed dashboard tools", () => {
  test("reads a thread without marking it viewed", async () => {
    const { client, queries } = await connect(() =>
      Response.json({ id: "t-1" })
    )
    const result = await client.callTool({
      name: "get_thread",
      arguments: { thread_id: "t/1" },
    })
    expect(result.isError).toBeFalsy()
    expect(result.content).toEqual([
      { type: "text", text: '{\n  "id": "t-1"\n}' },
    ])
    expect(queries[0]?.get("mark_viewed")).toBe("false")
  })

  test("denies admin tools to non-admin sessions before requesting workspace data", async () => {
    const { client, bodies } = await connect((url) =>
      url.pathname.endsWith("/me")
        ? Response.json({ login: "user", is_admin: false })
        : Response.json({ workspaces: [] })
    )
    const result = await client.callTool({
      name: "list_workspaces",
      arguments: {},
    })
    expect(result.isError).toBe(true)
    expect(JSON.stringify(result.content)).toContain("Only workspace admins")
    expect(bodies).toHaveLength(1)
  })

  test("exposes independent tools and keeps admin mutations behind identity checks", async () => {
    const { client, queries, bodies } = await connect((url) =>
      url.pathname.endsWith("/me")
        ? Response.json({ login: "admin", is_admin: true })
        : Response.json({ ok: true })
    )
    const catalog = await client.listTools()
    expect(catalog.tools.map((tool) => tool.name)).toContain(
      "create_automation"
    )
    expect(catalog.tools.map((tool) => tool.name)).toContain(
      "set_my_instructions"
    )
    expect(catalog.tools.map((tool) => tool.name)).not.toContain("execute")
    const result = await client.callTool({
      name: "create_skill",
      arguments: { name: "review", description: "Review code" },
    })
    expect(result.isError).toBeFalsy()
    expect(JSON.parse(bodies[0] ?? "null")).toEqual({
      name: "review",
      description: "Review code",
      instructions: "",
    })
    const adminResult = await client.callTool({
      name: "refresh_workspace",
      arguments: { slug: "default" },
    })
    expect(adminResult.isError).toBeFalsy()
    expect(queries).toHaveLength(3)
  })

  test("allows admins to read workspace data", async () => {
    const { client, bodies } = await connect((url) =>
      url.pathname.endsWith("/me")
        ? Response.json({ login: "admin", is_admin: true })
        : Response.json({ workspaces: [{ slug: "default" }] })
    )
    const result = await client.callTool({
      name: "list_workspaces",
      arguments: {},
    })
    expect(result.isError).toBeFalsy()
    expect(JSON.stringify(result.content)).toContain("default")
    expect(bodies).toHaveLength(2)
  })
})

describe("upload_session", () => {
  test("streams a header line and the transcript verbatim as gzipped JSONL", async () => {
    const dir = await mkdtemp(join(tmpdir(), "oswe-upload-"))
    stops.push(() => rm(dir, { recursive: true, force: true }))
    const transcript =
      '{"type":"user","uuid":"u1","message":{"content":"hi"}}\n'
    const path = join(dir, "session.jsonl")
    await writeFile(path, transcript)
    const { client, bodies, encodings } = await connect(() =>
      Response.json({ id: "t-9", title: "hi" })
    )

    const result = await client.callTool({
      name: "upload_session",
      arguments: {
        type: "claude",
        transcript_path: path,
        repo: "acme/web",
        branch: "fix-login",
      },
    })

    expect(result.isError).toBeFalsy()
    expect(encodings[0]).toBe("gzip")
    const body = bodies[0] ?? ""
    const newline = body.indexOf("\n")
    expect(JSON.parse(body.slice(0, newline))).toEqual({
      type: "claude",
      repo: "acme/web",
      branch: "fix-login",
      visibility: "workspace",
    })
    expect(body.slice(newline + 1)).toBe(transcript)
    expect(result.structuredContent).toMatchObject({
      thread_id: "t-9",
      url: expect.stringMatching(/\/agents\/t-9$/),
    })
  })

  test("refuses a PR link alongside a branch without calling the server", async () => {
    const { client, bodies } = await connect(() => Response.json({}))

    const result = await client.callTool({
      name: "upload_session",
      arguments: {
        type: "claude",
        transcript_path: "/nonexistent/session.jsonl",
        pr_url: "https://github.com/acme/web/pull/7",
        branch: "fix-login",
      },
    })

    expect(result.isError).toBe(true)
    expect(bodies).toEqual([])
  })
})
