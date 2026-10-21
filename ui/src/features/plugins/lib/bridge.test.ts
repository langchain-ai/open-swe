import { afterEach, describe, expect, it, vi } from "vitest"

import type { InstalledPlugin } from "@/features/plugins/lib/api"
import type { BridgeHost } from "@/features/plugins/lib/bridge"
import { handleBridgeRequest } from "@/features/plugins/lib/bridge"

const host = {
  plugin: { id: "install-1" } as InstalledPlugin,
} as BridgeHost

describe("handleBridgeRequest", () => {
  afterEach(() => vi.unstubAllGlobals())

  it.each([
    ["storage.delete", { collection: "c", key: "../../../../threads/t1" }],
    ["storage.get", { collection: "c", key: "a/../../other" }],
    ["storage.put", { collection: "..", key: "k", document: {} }],
    ["rooms.publish", { room: "../../install-2", update: new Uint8Array() }],
    ["github.file", { repository: "../..", path: "README.md", ref: "main" }],
  ])(
    "refuses %s with a dot segment before sending anything",
    async (method, params) => {
      const fetch = vi.fn()
      vi.stubGlobal("fetch", fetch)
      await expect(
        handleBridgeRequest(host, method, params)
      ).rejects.toMatchObject({
        status: 400,
      })
      expect(fetch).not.toHaveBeenCalled()
    }
  )
})
