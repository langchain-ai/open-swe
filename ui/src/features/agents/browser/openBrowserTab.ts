import type { PanelThreadRef } from "@/features/agents/lib/rightPanelStore"
import type { CloudBrowserSession } from "@/features/agents/browser/cloudBrowserSession"
import {
  type BrowserHostKind,
  newBrowserTabId,
  useBrowserTabStore,
} from "@/features/agents/browser/browserTabStore"
import { normalizeBrowserUrl } from "@/features/agents/browser/browserUrl"
import { useRightPanelStore } from "@/features/agents/lib/rightPanelStore"

/**
 * Opens `url` in a new tab of the thread's browser and makes it the active
 * surface. Desktop tabs get a client id and the webview loads the URL as its
 * first page; cloud tabs are created inside the sandbox browser, which
 * reports the new target back and the strip picks it up from there.
 */
export async function openBrowserTabWithUrl(input: {
  readonly threadRef: PanelThreadRef
  readonly host: BrowserHostKind
  readonly url: string
  readonly session: CloudBrowserSession | null
}): Promise<string> {
  const url = normalizeBrowserUrl(input.url)
  if (input.host === "cloud") {
    if (!input.session)
      throw new Error("The sandbox browser is not connected yet.")
    return input.session.createTab(url)
  }
  const tabId = newBrowserTabId()
  useBrowserTabStore.getState().upsertTab(input.threadRef, {
    tabId,
    host: "desktop",
    nav: { kind: "loading", url, title: "" },
  })
  useRightPanelStore.getState().openBrowser(input.threadRef, tabId)
  return tabId
}
