import { afterEach, describe, expect, it, vi } from "vitest"

import type { InstalledPlugin } from "@/features/plugins/lib/api"
import type { BridgeHost } from "@/features/plugins/lib/bridge"
import { PluginHostApi, SessionBudget } from "@/features/plugins/lib/bridge"

const host = {
  plugin: { id: "install-1", repositories: [] } as unknown as InstalledPlugin,
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
