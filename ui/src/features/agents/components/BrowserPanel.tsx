import { useEffect, useMemo, useRef, useState } from "react"
import { Banner } from "@langchain/macaw-components/Banner"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { Text } from "@langchain/macaw-components/Text"
import { GlobeIcon } from "@phosphor-icons/react/dist/ssr/Globe"

import { useAgentThreadBrowser } from "@/features/agents/lib/queries"

/** What LangSmith's window page posts to the page that frames it. */
type WindowMessage =
  | { type: "connection"; connected: boolean }
  | { type: "control"; controller: "agent" | "user"; reason: string }

function windowMessage(data: unknown): WindowMessage | null {
  if (typeof data !== "object" || data === null) return null
  const message = data as Record<string, unknown>
  if (message.source !== "langsmith-browser-window") return null
  if (message.type === "connection" && typeof message.connected === "boolean")
    return { type: "connection", connected: message.connected }
  if (
    message.type === "control" &&
    (message.controller === "agent" || message.controller === "user")
  )
    return {
      type: "control",
      controller: message.controller,
      reason: typeof message.reason === "string" ? message.reason : "",
    }
  return null
}

/**
 * The browser window a thread's agent opened, framed from LangSmith's own page
 * for it, zoomed in to follow the pointer in the narrow panel. The page has
 * the Take control and Hand back buttons; it tells this panel who is in
 * control, and why the agent asked for a person.
 */
export function BrowserPanel({ threadId }: { threadId: string }) {
  const browser = useAgentThreadBrowser(threadId, true)
  const frameRef = useRef<HTMLIFrameElement>(null)
  const [connected, setConnected] = useState(false)
  const [control, setControl] = useState<{
    controller: "agent" | "user"
    reason: string
  } | null>(null)

  const actUrl = browser.data?.act_url
  const frame = useMemo(() => {
    if (!actUrl) return null
    const url = new URL(actUrl)
    url.searchParams.set("fit", "follow")
    url.searchParams.set("zoom", "1")
    return { src: url.toString(), origin: url.origin }
  }, [actUrl])

  useEffect(() => {
    if (!frame) return
    const onMessage = (event: MessageEvent) => {
      if (
        event.origin !== frame.origin ||
        event.source !== frameRef.current?.contentWindow
      )
        return
      const message = windowMessage(event.data)
      if (message?.type === "connection") setConnected(message.connected)
      if (message?.type === "control")
        setControl({ controller: message.controller, reason: message.reason })
    }
    window.addEventListener("message", onMessage)
    return () => window.removeEventListener("message", onMessage)
  }, [frame])

  if (browser.isPending) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <Spinner />
      </div>
    )
  }
  if (!browser.data || !frame) {
    return (
      <div className="flex flex-1 items-center justify-center p-space-5">
        <EmptyState
          size="sm"
          icon={GlobeIcon}
          title="No browser to show"
          description={
            browser.error?.message ??
            "The agent hasn't opened a browser in this thread."
          }
        />
      </div>
    )
  }

  const tab = browser.data.tabs.at(-1)
  const status = !connected
    ? "Connecting…"
    : control?.controller === "user"
      ? "You're in control"
      : "The agent is in control"

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-space-2 border-b border-default px-space-3 py-space-2">
        <Text
          variant="sm"
          color="secondary"
          className="min-w-0 flex-1 truncate"
        >
          {tab?.title || tab?.url || "Browser"}
        </Text>
        <Text variant="xs" color="tertiary" className="shrink-0">
          {status}
        </Text>
      </div>
      {control?.controller === "agent" && control.reason ? (
        <div className="px-space-3 pt-space-2">
          <Banner intent="info" title="The agent needs you">
            {control.reason}
          </Banner>
        </div>
      ) : null}
      <iframe
        ref={frameRef}
        title="Browser window"
        src={frame.src}
        allow="clipboard-read; clipboard-write"
        className="min-h-0 w-full flex-1 border-0 bg-surface-level-2"
      />
    </div>
  )
}
