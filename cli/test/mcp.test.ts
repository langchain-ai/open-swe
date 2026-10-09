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
      if (url.pathname.endsWith("/cli/mcp/tools")) {
        const response = respond(url)
        if (
          response.headers.get("content-type")?.includes("application/json")
        ) {
          const value = await response.clone().json()
          if (Array.isArray(value)) return response
        }
        return Response.json([])
      }
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
  const server = await createMcpServer("0.0.0-test", async () => api)
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

describe("create_session", () => {
  test("starts by default and supports creating an idle session", async () => {
    const { client, bodies } = await connect((url) =>
      url.pathname === "/dashboard/api/threads"
        ? Response.json({ thread_id: "new-thread" }, { status: 201 })
        : new Response("not found", { status: 404 })
    )
    for (const start of [undefined, false]) {
      const result = await client.callTool({
        name: "create_session",
        arguments: {
          prompt: "Fix login",
          repo: "acme/web",
          workspace: "dev",
          ...(start === undefined ? {} : { start }),
        },
      })
      expect(result.isError).toBeFalsy()
      expect(result.structuredContent).toMatchObject({
        thread_id: "new-thread",
        url: expect.stringMatching(/\/agents\/new-thread$/),
      })
    }
    expect(bodies.map((body) => JSON.parse(body))).toEqual([
      { prompt: "Fix login", repo: "acme/web", workspace: "dev", start: true },
      { prompt: "Fix login", repo: "acme/web", workspace: "dev", start: false },
    ])
  })
})

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

describe("exposed Python tools", () => {
  const schema = {
    name: "manage_feature_flags",
    description: "Manage feature flags from Python",
    access: "admin",
    parameters: {
      type: "object",
      properties: { action: { type: "string", enum: ["read", "set"] } },
      required: ["action"],
    },
  }

  test("uses backend schemas and calls the backend with validated arguments", async () => {
    const { client, bodies, queries } = await connect((url) =>
      url.pathname.endsWith("/cli/mcp/tools")
        ? Response.json([schema])
        : Response.json({ status: "success", content: { scope: "instance" } })
    )
    const catalog = await client.listTools()
    expect(
      catalog.tools.find((tool) => tool.name === schema.name)?.inputSchema
    ).toMatchObject(schema.parameters)
    expect(catalog.tools.map((tool) => tool.name)).not.toContain("execute")
    const invalid = await client.callTool({
      name: schema.name,
      arguments: { action: "explode" },
    })
    expect(invalid.isError).toBe(true)
    expect(queries).toHaveLength(0)
    const result = await client.callTool({
      name: schema.name,
      arguments: { action: "read" },
    })
    expect(result.isError).toBeFalsy()
    expect(JSON.parse(bodies[0] ?? "null")).toEqual({ action: "read" })
  })

  test("keeps no-argument tools when another remote schema is unsupported", async () => {
    const { client } = await connect((url) =>
      url.pathname.endsWith("/cli/mcp/tools")
        ? Response.json([
            { ...schema, name: "no_args", parameters: { type: "object" } },
            { ...schema, name: "bad_schema", parameters: { type: "array" } },
          ])
        : Response.json({ status: "success", content: "done" })
    )
    const catalog = await client.listTools()
    expect(catalog.tools.map((tool) => tool.name)).toContain("no_args")
    expect(catalog.tools.map((tool) => tool.name)).not.toContain("bad_schema")
    const result = await client.callTool({ name: "no_args", arguments: {} })
    expect(result.isError).toBeFalsy()
  })

  test("does not expose tools missing from the signed-in user's catalog", async () => {
    const { client } = await connect(() => Response.json([]))
    const catalog = await client.listTools()
    expect(catalog.tools.map((tool) => tool.name)).not.toContain(schema.name)
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

describe("human review", () => {
  test("request_human_review posts the summary to the pull request's review endpoint", async () => {
    const paths: string[] = []
    const { client, bodies } = await connect((url) => {
      if (!url.pathname.endsWith("/cli/mcp/tools")) paths.push(url.pathname)
      return Response.json({
        success: true,
        error: "",
        request_id: "r-1",
        channel: "C1",
        permalink: "https://slack.example/p1",
        reused: false,
        summary_updated: false,
      })
    })

    const result = await client.callTool({
      name: "request_human_review",
      arguments: {
        pr_url: "https://github.com/acme/web/pull/7",
        inline_summary: "Keeps the login form's error visible after a retry.",
      },
    })

    expect(result.isError).toBeFalsy()
    expect(paths).toEqual([
      "/dashboard/api/repos/acme/web/pulls/7/human-review",
    ])
    expect(JSON.parse(bodies[0] ?? "")).toEqual({
      inline_summary: "Keeps the login form's error visible after a retry.",
    })
    expect(result.structuredContent).toEqual({
      request_id: "r-1",
      channel: "C1",
      reused: false,
      summary_updated: false,
    })
  })

  test("dismiss_human_review_request posts to the dismiss endpoint", async () => {
    const paths: string[] = []
    const { client, bodies } = await connect((url) => {
      if (!url.pathname.endsWith("/cli/mcp/tools")) paths.push(url.pathname)
      return Response.json({ request_id: "r-1" })
    })

    const result = await client.callTool({
      name: "dismiss_human_review_request",
      arguments: {
        pr_url: "https://github.com/acme/web/pull/7",
        reason: "wrong summary",
      },
    })

    expect(result.isError).toBeFalsy()
    expect(paths).toEqual([
      "/dashboard/api/repos/acme/web/pulls/7/human-review/dismiss",
    ])
    expect(JSON.parse(bodies[0] ?? "")).toEqual({ reason: "wrong summary" })
    expect(result.structuredContent).toEqual({ request_id: "r-1" })
  })
})
