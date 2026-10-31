/** @vitest-environment jsdom */
import { Collection, PluginHost } from "@openswe/plugin-sdk"
import type { CollectionApi, HostApi, HostContext } from "@openswe/plugin-sdk"
import { newMessagePortRpcSession } from "capnweb"
import type { RpcStub } from "capnweb"
import { afterEach, describe, expect, it, vi } from "vitest"

import type { InstalledPlugin } from "@/features/plugins/lib/api"
import type { BridgeHost } from "@/features/plugins/lib/bridge"
import { PluginHostApi, SessionBudget } from "@/features/plugins/lib/bridge"

const host = {
  plugin: {
    id: "install-1",
    repositories: ["acme/app"],
    manifest: { permissions: ["storage", "agent"] },
  } as unknown as InstalledPlugin,
} as BridgeHost

function hostApi(budget = new SessionBudget()) {
  return new PluginHostApi(() => host, budget)
}

describe("PluginHostApi", () => {
  afterEach(() => vi.unstubAllGlobals())

  it.each([
    [
      "a key with dot segments",
      async (api: PluginHostApi) =>
        (await api.collection("c", {})).delete("../../../../threads/t1"),
    ],
    [
      "a key that climbs out",
      async (api: PluginHostApi) =>
        (await api.collection("c", {})).get("a/../../other"),
    ],
    ["a dot collection", (api: PluginHostApi) => api.collection("..", {})],
    [
      "a dot room",
      (api: PluginHostApi) =>
        api.joinRoom({ room: "../../install-2" }, () => {}, 0),
    ],
    ["a dot repository", (api: PluginHostApi) => api.github("../..")],
  ])("refuses %s before sending anything", async (_name, call) => {
    const fetch = vi.fn()
    vi.stubGlobal("fetch", fetch)
    await expect(call(hostApi())).rejects.toMatchObject({ status: 400 })
    expect(fetch).not.toHaveBeenCalled()
  })

  it("refuses capabilities past the session's allowance", async () => {
    const api = hostApi(
      new SessionBudget({
        concurrency: 4,
        pending: 16,
        perMinute: 100,
        capabilities: 2,
        backlog: 8,
      })
    )
    await api.collection("a", {})
    await api.collection("b", {})
    await expect(api.collection("c", {})).rejects.toMatchObject({ status: 429 })
  })
})

describe("the agent's tools over Cap'n Web", () => {
  it("run in the plugin and return its result to the dock", async () => {
    let offered: Parameters<BridgeHost["provideTools"]> | null = null
    const agentHost = {
      ...host,
      subscribe: () => () => {},
      provideTools: (...args: Parameters<BridgeHost["provideTools"]>) => {
        offered = args
        return () => {}
      },
    } as BridgeHost
    const { port1, port2 } = new MessageChannel()
    using session = newMessagePortRpcSession(
      port1,
      new PluginHostApi(() => agentHost, new SessionBudget())
    )
    void session
    const remote = newMessagePortRpcSession<HostApi>(port2)
    const plugin = new (PluginHost as unknown as new (
      api: RpcStub<HostApi>,
      context: HostContext
    ) => PluginHost)(remote as unknown as RpcStub<HostApi>, {} as HostContext)

    plugin.agent.provide({
      read_page: {
        description: "The open page",
        parameters: {
          type: "object",
          properties: { path: { type: "string" } },
        },
        run: (args) => ({ path: args.path ?? null, markdown: "# Hi" }),
      },
    })
    await vi.waitFor(() => expect(offered).not.toBeNull())
    const [offer, run] = offered!

    expect(offer.tools.map((tool) => tool.name)).toEqual(["read_page"])
    expect(JSON.parse(await run("read_page", { path: "a.md" }))).toEqual({
      path: "a.md",
      markdown: "# Hi",
    })
    await expect(run("delete_everything", {})).rejects.toThrow(
      "not one of this plugin's tools"
    )
  })

  it("refuse a chat about a repository the person did not share", async () => {
    const chooseChat = vi.fn()
    const api = new PluginHostApi(
      () => ({ ...host, chooseChat }) as BridgeHost,
      new SessionBudget()
    )
    await expect(
      api.chooseChat({ name: "repo", repository: "evil/secrets" })
    ).rejects.toMatchObject({ status: 403 })
    await api.chooseChat({ name: "repo", repository: "acme/app" })
    expect(chooseChat).toHaveBeenCalledWith({
      name: "repo",
      repository: "acme/app",
    })
  })
})

describe("documents over Cap'n Web", () => {
  afterEach(() => vi.unstubAllGlobals())

  it("keep keys named like Object.prototype members", async () => {
    // The backend echoes what it stores.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: RequestInit) => {
        const body = JSON.parse(String(init.body)) as { document: object }
        return new Response(
          JSON.stringify({
            key: "k",
            document: body.document,
            updated_at: null,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      })
    )
    const { port1, port2 } = new MessageChannel()
    using session = newMessagePortRpcSession(port1, hostApi())
    void session
    const remote = newMessagePortRpcSession<HostApi>(port2)
    const notes = new Collection<string, { tags: Record<string, number> }>(
      remote.collection("notes", {}) as unknown as RpcStub<CollectionApi>,
      "notes",
      {}
    )

    const stored = await notes.put("k", {
      tags: { constructor: 1, toString: 2, plain: 3 },
    })

    expect(stored.document.tags).toEqual({
      constructor: 1,
      toString: 2,
      plain: 3,
    })
  })

  it("page after a document by its field's value and its key", async () => {
    const fetch = vi.fn(
      async () =>
        new Response("[]", {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
    )
    vi.stubGlobal("fetch", fetch)
    const { port1, port2 } = new MessageChannel()
    using session = newMessagePortRpcSession(port1, hostApi())
    void session
    const remote = newMessagePortRpcSession<HostApi>(port2)
    const tasks = new Collection<string, { at: { rank: number } }>(
      remote.collection("tasks", {}) as unknown as RpcStub<CollectionApi>,
      "tasks",
      {}
    )
    const last = { key: "t-7", document: { at: { rank: 3 } }, updatedAt: null }

    await tasks.query({
      orderBy: "at.rank",
      direction: "desc",
      startAfter: last,
    })
    await tasks.query({ prefix: "t-", endBefore: last })

    const bodies = fetch.mock.calls.map(
      (call: unknown[]) =>
        JSON.parse(String((call[1] as RequestInit).body)) as object
    )
    expect(bodies).toMatchObject([
      {
        order_by: "at.rank",
        direction: "desc",
        start: { value: 3, key: "t-7", inclusive: false },
      },
      { prefix: "t-", end: { value: "t-7", inclusive: false } },
    ])
  })
})
