import { beforeEach, describe, expect, it } from "vitest"

import {
  migratePersistedBrowserTabs,
  selectThreadBrowserTabs,
  useBrowserTabStore,
} from "@/features/agents/browser/browserTabStore"

const ref = { scope: "cloud" as const, threadId: "thread-1" }
const tabs = () =>
  selectThreadBrowserTabs(useBrowserTabStore.getState().tabsByThreadKey, ref)

describe("browser tab store", () => {
  beforeEach(() => {
    useBrowserTabStore.setState({
      tabsByThreadKey: {},
      connectionByThreadKey: {},
    })
  })

  it("creates tabs with defaults and patches them in place", () => {
    const { upsertTab, patchTab } = useBrowserTabStore.getState()
    upsertTab(ref, { tabId: "t1", host: "cloud" })
    expect(tabs().t1).toMatchObject({
      nav: { kind: "idle" },
      zoomFactor: 1,
      viewport: { mode: "fill" },
      attached: false,
    })
    patchTab(ref, "t1", {
      nav: { kind: "loading", url: "http://localhost:5173/", title: "" },
      attached: true,
    })
    expect(tabs().t1?.nav).toEqual({
      kind: "loading",
      url: "http://localhost:5173/",
      title: "",
    })
    // Patching an unknown tab is a no-op rather than a resurrection.
    patchTab(ref, "missing", { attached: true })
    expect(Object.keys(tabs())).toEqual(["t1"])
  })

  it("drops the thread entry once its last tab closes", () => {
    const { upsertTab, removeTab } = useBrowserTabStore.getState()
    upsertTab(ref, { tabId: "t1", host: "desktop" })
    removeTab(ref, "t1")
    expect(useBrowserTabStore.getState().tabsByThreadKey).toEqual({})
  })

  it("caps the install log", () => {
    const { appendInstallOutput } = useBrowserTabStore.getState()
    for (let index = 0; index < 450; index += 1) {
      appendInstallOutput(ref, `line ${index}`)
    }
    const connection =
      useBrowserTabStore.getState().connectionByThreadKey["cloud:thread-1"]
    expect(connection?.installOutput).toHaveLength(400)
    expect(connection?.installOutput[0]).toBe("line 50")
  })
})

describe("migratePersistedBrowserTabs", () => {
  it("restores the last url as a reloadable page and resets transient fields", () => {
    const migrated = migratePersistedBrowserTabs({
      tabsByThreadKey: {
        "local:s1": {
          t1: {
            tabId: "t1",
            host: "desktop",
            nav: {
              kind: "loading",
              url: "http://localhost:3000/",
              title: "Dev",
            },
            zoomFactor: 1.25,
            viewport: {
              mode: "preset",
              width: 375,
              height: 667,
              presetId: "iphone-se",
            },
            colorScheme: "dark",
            attached: true,
            canGoBack: true,
          },
        },
      },
    })
    expect(migrated.tabsByThreadKey["local:s1"]?.t1).toMatchObject({
      nav: { kind: "success", url: "http://localhost:3000/", title: "Dev" },
      zoomFactor: 1.25,
      viewport: { mode: "preset", presetId: "iphone-se" },
      colorScheme: "dark",
      attached: false,
      canGoBack: false,
      favicon: null,
    })
  })

  it("drops entries it cannot trust", () => {
    const migrated = migratePersistedBrowserTabs({
      tabsByThreadKey: {
        "cloud:x": {
          t1: { tabId: "other", host: "cloud" },
          t2: { tabId: "t2", host: "unknown" },
          t3: {
            tabId: "t3",
            host: "cloud",
            nav: { kind: "success", url: "javascript:alert(1)" },
            zoomFactor: 999,
            colorScheme: "sepia",
          },
        },
        garbage: "nope",
      },
    })
    expect(Object.keys(migrated.tabsByThreadKey)).toEqual(["cloud:x"])
    expect(Object.keys(migrated.tabsByThreadKey["cloud:x"] ?? {})).toEqual([
      "t3",
    ])
    expect(migrated.tabsByThreadKey["cloud:x"]?.t3).toMatchObject({
      nav: { kind: "idle" },
      zoomFactor: 1,
      colorScheme: "system",
    })
    expect(migratePersistedBrowserTabs(undefined)).toEqual({
      tabsByThreadKey: {},
    })
  })
})
