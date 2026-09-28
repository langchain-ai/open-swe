/**
 * Runtime state of every browser tab, keyed by thread and tab id.
 *
 * Both hosts write here: the desktop webview host mirrors the main-process
 * tab state it receives over IPC, and the cloud host mirrors what the sandbox
 * Chromium reports over the DevTools protocol. The panel chrome only reads.
 *
 * Tab identity, the last URL and the display preferences are persisted so a
 * reload of the app brings the same tabs back; everything else is transient
 * and re-derived once a host attaches.
 */
import { create } from "zustand"
import { createJSONStorage, persist } from "zustand/middleware"

import type { PanelThreadRef } from "@/features/agents/lib/rightPanelStore"
import {
  type BrowserViewport,
  DEFAULT_BROWSER_ZOOM,
  FILL_VIEWPORT,
  parseBrowserViewport,
} from "@/features/agents/browser/browserViewport"
import { isWebUrl } from "@/features/agents/browser/browserUrl"
import { scopedThreadKey } from "@/features/agents/lib/rightPanelStore"

export type BrowserHostKind = "desktop" | "cloud"
export type BrowserColorScheme = "system" | "light" | "dark"

export type BrowserNavStatus =
  | { readonly kind: "idle" }
  | { readonly kind: "loading"; readonly url: string; readonly title: string }
  | { readonly kind: "success"; readonly url: string; readonly title: string }
  | {
      readonly kind: "failed"
      readonly url: string
      readonly title: string
      readonly code: number | null
      readonly description: string
    }

export interface BrowserTabState {
  readonly tabId: string
  readonly host: BrowserHostKind
  readonly nav: BrowserNavStatus
  readonly canGoBack: boolean
  readonly canGoForward: boolean
  readonly zoomFactor: number
  readonly viewport: BrowserViewport
  readonly colorScheme: BrowserColorScheme
  /** Image URL the host resolved for the current page, or null. */
  readonly favicon: string | null
  /** True once a host renders this tab and accepts commands for it. */
  readonly attached: boolean
  readonly updatedAt: number
}

export type CloudBrowserConnectionStatus =
  | "idle"
  | "connecting"
  | "starting"
  | "browser-missing"
  | "installing"
  | "ready"
  | "error"
  | "closed"

export interface CloudBrowserConnectionState {
  readonly status: CloudBrowserConnectionStatus
  readonly error: string | null
  readonly installCommand: string | null
  readonly installOutput: ReadonlyArray<string>
}

export const IDLE_CLOUD_BROWSER_CONNECTION: CloudBrowserConnectionState =
  Object.freeze({
    status: "idle",
    error: null,
    installCommand: null,
    installOutput: [],
  })

const MAX_INSTALL_OUTPUT_LINES = 400
const BROWSER_TABS_STORAGE_KEY = "open-swe:browser-tabs"
const BROWSER_TABS_STORAGE_VERSION = 1
const MIN_ZOOM = 0.1
const MAX_ZOOM = 10

type TabPatch = Partial<Omit<BrowserTabState, "tabId" | "host">>

interface BrowserTabStoreState {
  tabsByThreadKey: Record<string, Record<string, BrowserTabState>>
  connectionByThreadKey: Record<string, CloudBrowserConnectionState>
  upsertTab: (
    ref: PanelThreadRef,
    tab: { tabId: string; host: BrowserHostKind } & TabPatch
  ) => void
  patchTab: (ref: PanelThreadRef, tabId: string, patch: TabPatch) => void
  removeTab: (ref: PanelThreadRef, tabId: string) => void
  setConnection: (
    ref: PanelThreadRef,
    patch: Partial<CloudBrowserConnectionState>
  ) => void
  appendInstallOutput: (ref: PanelThreadRef, line: string) => void
  removeThread: (ref: PanelThreadRef) => void
}

export const EMPTY_BROWSER_TABS: Readonly<Record<string, BrowserTabState>> =
  Object.freeze({})

export function newBrowserTabId(): string {
  return `tab_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`
}

export function defaultBrowserTab(
  tabId: string,
  host: BrowserHostKind
): BrowserTabState {
  return {
    tabId,
    host,
    nav: { kind: "idle" },
    canGoBack: false,
    canGoForward: false,
    zoomFactor: DEFAULT_BROWSER_ZOOM,
    viewport: FILL_VIEWPORT,
    colorScheme: "system",
    favicon: null,
    attached: false,
    updatedAt: 0,
  }
}

export function browserTabUrl(
  tab: BrowserTabState | null | undefined
): string | null {
  if (!tab || tab.nav.kind === "idle") return null
  return tab.nav.url
}

function isColorScheme(value: unknown): value is BrowserColorScheme {
  return value === "system" || value === "light" || value === "dark"
}

/**
 * Persisted tab state is untrusted input: each field is re-validated and a
 * tab that no longer makes sense is dropped. Transient fields reset so the
 * host re-establishes them on attach.
 */
export function migratePersistedBrowserTabs(persistedState: unknown): {
  tabsByThreadKey: Record<string, Record<string, BrowserTabState>>
} {
  if (!persistedState || typeof persistedState !== "object") {
    return { tabsByThreadKey: {} }
  }
  const raw = (persistedState as { tabsByThreadKey?: unknown }).tabsByThreadKey
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return { tabsByThreadKey: {} }
  }
  const tabsByThreadKey: Record<string, Record<string, BrowserTabState>> = {}
  for (const [threadKey, tabs] of Object.entries(
    raw as Record<string, unknown>
  )) {
    if (!tabs || typeof tabs !== "object" || Array.isArray(tabs)) continue
    const restored: Record<string, BrowserTabState> = {}
    for (const [tabId, value] of Object.entries(
      tabs as Record<string, unknown>
    )) {
      if (!value || typeof value !== "object") continue
      const record = value as Record<string, unknown>
      if (record.tabId !== tabId || typeof tabId !== "string" || !tabId)
        continue
      if (record.host !== "desktop" && record.host !== "cloud") continue
      const navRecord =
        record.nav && typeof record.nav === "object"
          ? (record.nav as Record<string, unknown>)
          : null
      const url =
        typeof navRecord?.url === "string" && isWebUrl(navRecord.url)
          ? navRecord.url
          : null
      const title = typeof navRecord?.title === "string" ? navRecord.title : ""
      const zoomFactor =
        typeof record.zoomFactor === "number" &&
        Number.isFinite(record.zoomFactor) &&
        record.zoomFactor >= MIN_ZOOM &&
        record.zoomFactor <= MAX_ZOOM
          ? record.zoomFactor
          : DEFAULT_BROWSER_ZOOM
      restored[tabId] = {
        ...defaultBrowserTab(tabId, record.host),
        nav: url ? { kind: "success", url, title } : { kind: "idle" },
        zoomFactor,
        viewport: parseBrowserViewport(record.viewport),
        colorScheme: isColorScheme(record.colorScheme)
          ? record.colorScheme
          : "system",
      }
    }
    if (Object.keys(restored).length > 0) tabsByThreadKey[threadKey] = restored
  }
  return { tabsByThreadKey }
}

const memoryStorage = (): Storage => {
  const map = new Map<string, string>()
  return {
    get length() {
      return map.size
    },
    clear: () => map.clear(),
    getItem: (key) => map.get(key) ?? null,
    key: (index) => [...map.keys()][index] ?? null,
    removeItem: (key) => void map.delete(key),
    setItem: (key, value) => void map.set(key, value),
  }
}

export const useBrowserTabStore = create<BrowserTabStoreState>()(
  persist(
    (set) => ({
      tabsByThreadKey: {},
      connectionByThreadKey: {},
      upsertTab: (ref, tab) =>
        set((state) => {
          const threadKey = scopedThreadKey(ref)
          const tabs = state.tabsByThreadKey[threadKey] ?? {}
          const { tabId, host, ...patch } = tab
          const current = tabs[tabId] ?? defaultBrowserTab(tabId, host)
          return {
            tabsByThreadKey: {
              ...state.tabsByThreadKey,
              [threadKey]: {
                ...tabs,
                [tabId]: { ...current, ...patch, host, updatedAt: Date.now() },
              },
            },
          }
        }),
      patchTab: (ref, tabId, patch) =>
        set((state) => {
          const threadKey = scopedThreadKey(ref)
          const tabs = state.tabsByThreadKey[threadKey]
          const current = tabs?.[tabId]
          if (!tabs || !current) return state
          return {
            tabsByThreadKey: {
              ...state.tabsByThreadKey,
              [threadKey]: {
                ...tabs,
                [tabId]: { ...current, ...patch, updatedAt: Date.now() },
              },
            },
          }
        }),
      removeTab: (ref, tabId) =>
        set((state) => {
          const threadKey = scopedThreadKey(ref)
          const tabs = state.tabsByThreadKey[threadKey]
          if (!tabs || !(tabId in tabs)) return state
          const { [tabId]: _removed, ...rest } = tabs
          const tabsByThreadKey = { ...state.tabsByThreadKey }
          if (Object.keys(rest).length === 0) delete tabsByThreadKey[threadKey]
          else tabsByThreadKey[threadKey] = rest
          return { tabsByThreadKey }
        }),
      setConnection: (ref, patch) =>
        set((state) => {
          const threadKey = scopedThreadKey(ref)
          const current =
            state.connectionByThreadKey[threadKey] ??
            IDLE_CLOUD_BROWSER_CONNECTION
          return {
            connectionByThreadKey: {
              ...state.connectionByThreadKey,
              [threadKey]: { ...current, ...patch },
            },
          }
        }),
      appendInstallOutput: (ref, line) =>
        set((state) => {
          const threadKey = scopedThreadKey(ref)
          const current =
            state.connectionByThreadKey[threadKey] ??
            IDLE_CLOUD_BROWSER_CONNECTION
          return {
            connectionByThreadKey: {
              ...state.connectionByThreadKey,
              [threadKey]: {
                ...current,
                installOutput: [...current.installOutput, line].slice(
                  -MAX_INSTALL_OUTPUT_LINES
                ),
              },
            },
          }
        }),
      removeThread: (ref) =>
        set((state) => {
          const threadKey = scopedThreadKey(ref)
          const { [threadKey]: _tabs, ...tabsByThreadKey } =
            state.tabsByThreadKey
          const { [threadKey]: _connection, ...connectionByThreadKey } =
            state.connectionByThreadKey
          return { tabsByThreadKey, connectionByThreadKey }
        }),
    }),
    {
      name: BROWSER_TABS_STORAGE_KEY,
      version: BROWSER_TABS_STORAGE_VERSION,
      storage: createJSONStorage(() =>
        typeof window === "undefined" ? memoryStorage() : window.localStorage
      ),
      partialize: (state) => ({
        tabsByThreadKey: Object.fromEntries(
          Object.entries(state.tabsByThreadKey).map(([threadKey, tabs]) => [
            threadKey,
            Object.fromEntries(
              Object.entries(tabs).map(([tabId, tab]) => [
                tabId,
                {
                  tabId: tab.tabId,
                  host: tab.host,
                  nav:
                    tab.nav.kind === "idle"
                      ? { kind: "idle" }
                      : {
                          kind: "success",
                          url: tab.nav.url,
                          title: tab.nav.title,
                        },
                  zoomFactor: tab.zoomFactor,
                  viewport: tab.viewport,
                  colorScheme: tab.colorScheme,
                },
              ])
            ),
          ])
        ),
      }),
      migrate: migratePersistedBrowserTabs,
      merge: (persistedState, currentState) => ({
        ...currentState,
        ...migratePersistedBrowserTabs(persistedState),
      }),
    }
  )
)

export function selectThreadBrowserTabs(
  tabsByThreadKey: Record<string, Record<string, BrowserTabState>>,
  ref: PanelThreadRef | null | undefined
): Readonly<Record<string, BrowserTabState>> {
  if (!ref) return EMPTY_BROWSER_TABS
  return tabsByThreadKey[scopedThreadKey(ref)] ?? EMPTY_BROWSER_TABS
}

export function selectBrowserTab(
  tabsByThreadKey: Record<string, Record<string, BrowserTabState>>,
  ref: PanelThreadRef,
  tabId: string
): BrowserTabState | null {
  return tabsByThreadKey[scopedThreadKey(ref)]?.[tabId] ?? null
}

export function selectCloudBrowserConnection(
  connectionByThreadKey: Record<string, CloudBrowserConnectionState>,
  ref: PanelThreadRef
): CloudBrowserConnectionState {
  return (
    connectionByThreadKey[scopedThreadKey(ref)] ?? IDLE_CLOUD_BROWSER_CONNECTION
  )
}
