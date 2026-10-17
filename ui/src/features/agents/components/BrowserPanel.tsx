import { useRef, useState } from "react"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Input } from "@langchain/macaw-components/Input"
import { Select } from "@langchain/macaw-components/Select"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { Text } from "@langchain/macaw-components/Text"
import { ArrowClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowClockwise"
import { ArrowLeftIcon } from "@phosphor-icons/react/dist/ssr/ArrowLeft"
import { ArrowRightIcon } from "@phosphor-icons/react/dist/ssr/ArrowRight"
import { GlobeIcon } from "@phosphor-icons/react/dist/ssr/Globe"
import type { MouseEvent as ReactMouseEvent, WheelEvent } from "react"

import type { ViewerMessage } from "@/features/agents/lib/browserInput"
import {
  addressUrl,
  keyDownMessage,
  keyUpMessage,
  modifiers,
  pagePoint,
} from "@/features/agents/lib/browserInput"
import { useBrowserView } from "@/features/agents/lib/browserView"
import {
  useAgentThreadBrowser,
  useSetThreadBrowserController,
} from "@/features/agents/lib/queries"
import { cn } from "@/lib/utils"

const MOUSE_MOVE_INTERVAL_MS = 30
const BUTTON_NAMES = ["left", "middle", "right"]
const SCOPE_LABELS = {
  private: "Your browser",
  workspace: "Workspace browser",
} as const

/**
 * The live view of the browser window a thread's agent opened. Anyone who can
 * prompt the thread can watch it, take it over to click and type (to sign in,
 * say), and hand it back to the agent.
 */
export function BrowserPanel({
  threadId,
  tabId: pinnedTabId,
}: {
  threadId: string
  /** The tab this surface shows; null follows the window's newest tab. */
  tabId: string | null
}) {
  const browser = useAgentThreadBrowser(threadId, true)
  const setController = useSetThreadBrowserController(threadId)
  const [chosenTabId, setChosenTabId] = useState<string | null>(null)
  const tabs = browser.data?.tabs ?? []
  const tabId =
    [pinnedTabId, chosenTabId].find(
      (id) => id && tabs.some((tab) => tab.id === id)
    ) ??
    tabs.at(-1)?.id ??
    null
  const tab = tabs.find((entry) => entry.id === tabId)
  const view = useBrowserView(threadId, tabId)
  const [draftAddress, setDraftAddress] = useState<string | null>(null)
  // An address the person entered, shown until the tab's URL moves on from
  // the one it had then, since the tab reports its new URL only later.
  const [entered, setEntered] = useState<{ url: string; from: string } | null>(
    null
  )
  const stageRef = useRef<HTMLDivElement>(null)
  const screenRef = useRef<HTMLImageElement>(null)
  const lastMoveRef = useRef(0)

  if (browser.isPending) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <Spinner />
      </div>
    )
  }
  if (!browser.data) {
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

  const isHuman = browser.data.controller === "user"
  const canDrive = isHuman && view.connected
  const sendInput = (message: ViewerMessage) => canDrive && view.send(message)
  const tabUrl = view.url ?? tab?.url ?? ""
  const shownUrl = entered?.from === tabUrl ? entered.url : tabUrl
  const address = draftAddress ?? (shownUrl === "about:blank" ? "" : shownUrl)

  const point = (event: ReactMouseEvent | WheelEvent) => {
    const screen = screenRef.current
    if (!screen || !view.metadata) return { x: 0, y: 0 }
    return pagePoint(
      event.clientX,
      event.clientY,
      screen.getBoundingClientRect(),
      view.metadata
    )
  }
  const mouse = (type: string, event: ReactMouseEvent) =>
    sendInput({
      type: "mouse",
      event: type,
      ...point(event),
      button: BUTTON_NAMES[event.button] ?? "left",
      buttons: event.buttons,
      clickCount: event.detail || 1,
      modifiers: modifiers(event),
    })
  const navigate = () => {
    const url = addressUrl(address)
    if (!url || !sendInput({ type: "navigate", url })) return
    setEntered({ url, from: tabUrl })
    setDraftAddress(null)
    stageRef.current?.focus()
  }
  const aspect = view.metadata?.deviceHeight
    ? view.metadata.deviceWidth / view.metadata.deviceHeight
    : 16 / 10

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-space-2 border-b border-default px-space-3 py-space-2">
        <div className="min-w-0 flex-1">
          {tabs.length > 1 && tabId ? (
            <Select
              size="sm"
              aria-label="Browser tab"
              value={tabId}
              onChange={(id) => {
                if (id) setChosenTabId(id)
              }}
              options={tabs.map((entry) => ({
                value: entry.id,
                label: entry.title || entry.url || "New tab",
              }))}
            />
          ) : (
            <Text variant="sm" color="secondary" className="truncate">
              {tab?.title || SCOPE_LABELS[browser.data.scope]}
            </Text>
          )}
        </div>
        <Text variant="xs" color="tertiary" className="shrink-0">
          {SCOPE_LABELS[browser.data.scope]}
        </Text>
        <Button
          size="xs"
          color={isHuman ? "primary" : "secondary"}
          variant={isHuman ? "normal" : "outlined"}
          disabled={setController.isPending}
          onClick={() => setController.mutate(isHuman ? "agent" : "user")}
        >
          {isHuman ? "Hand back" : "Take control"}
        </Button>
      </div>
      {browser.data.reason && !isHuman ? (
        <div className="px-space-3 pt-space-2">
          <Banner intent="info" title="The agent needs you">
            {browser.data.reason}
          </Banner>
        </div>
      ) : null}
      <form
        className="flex items-center gap-space-1 px-space-3 py-space-2"
        onSubmit={(event) => {
          event.preventDefault()
          navigate()
        }}
      >
        <IconButton
          icon={ArrowLeftIcon}
          label="Back"
          variant="plain"
          size="sm"
          disabled={!canDrive}
          onClick={() => sendInput({ type: "back" })}
        />
        <IconButton
          icon={ArrowRightIcon}
          label="Forward"
          variant="plain"
          size="sm"
          disabled={!canDrive}
          onClick={() => sendInput({ type: "forward" })}
        />
        <IconButton
          icon={ArrowClockwiseIcon}
          label="Reload"
          variant="plain"
          size="sm"
          disabled={!canDrive}
          onClick={() => sendInput({ type: "reload" })}
        />
        <Input
          size="sm"
          className="flex-1"
          aria-label="Address"
          value={address}
          debounceMs={0}
          disabled={!canDrive}
          placeholder={
            isHuman
              ? "Type a URL and press Enter"
              : "Take control to type a URL"
          }
          onChange={setDraftAddress}
        />
      </form>
      <div
        ref={stageRef}
        role="application"
        aria-label="Browser window"
        tabIndex={0}
        // The frame sizes itself against the stage's width and height.
        style={{ containerType: "size" }}
        className={cn(
          "relative flex min-h-0 flex-1 items-center justify-center overflow-hidden bg-surface-level-2 outline-none",
          canDrive ? "cursor-default" : "cursor-not-allowed"
        )}
        onKeyDown={(event) => {
          if (!canDrive) return
          const message = keyDownMessage(event)
          if (!message) return
          event.preventDefault()
          sendInput(message)
        }}
        onKeyUp={(event) => {
          if (!canDrive) return
          event.preventDefault()
          sendInput(keyUpMessage(event))
        }}
        onPaste={(event) => {
          if (!canDrive) return
          event.preventDefault()
          sendInput({ type: "text", text: event.clipboardData.getData("text") })
        }}
      >
        {view.frame ? (
          <img
            ref={screenRef}
            src={view.frame}
            alt="Live view of the browser tab"
            draggable={false}
            className="h-auto select-none"
            style={{ width: `min(100cqw, 100cqh * ${aspect})` }}
            onMouseDown={(event) => {
              event.preventDefault()
              stageRef.current?.focus()
              mouse("mousePressed", event)
            }}
            onMouseUp={(event) => mouse("mouseReleased", event)}
            onMouseMove={(event) => {
              const now = performance.now()
              if (now - lastMoveRef.current < MOUSE_MOVE_INTERVAL_MS) return
              lastMoveRef.current = now
              sendInput({
                type: "mouse",
                event: "mouseMoved",
                ...point(event),
                button: "none",
                buttons: event.buttons,
                modifiers: modifiers(event),
              })
            }}
            onWheel={(event) =>
              sendInput({
                type: "mouse",
                event: "mouseWheel",
                ...point(event),
                button: "none",
                deltaX: event.deltaX,
                deltaY: event.deltaY,
                modifiers: modifiers(event),
              })
            }
            onContextMenu={(event) => event.preventDefault()}
          />
        ) : (
          <Text variant="sm" color="tertiary">
            {view.error ??
              (view.connected
                ? "Waiting for the page…"
                : "Connecting to the browser…")}
          </Text>
        )}
      </div>
    </div>
  )
}
