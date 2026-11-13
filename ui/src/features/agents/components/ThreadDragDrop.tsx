import { type ReactNode, useCallback, useRef, useState } from "react"
import type { RefObject } from "react"

import {
  SidebarThreadDragPreviewContext,
  type ThreadDragItem,
} from "@/features/agents/components/ThreadDragDrop.utils"
import { ThreadDragPreview } from "@/features/agents/components/ThreadDragPreview"
import { useSidebarThreadSelection } from "@/features/agents/components/SidebarThreadSelection"
import {
  type ThreadDropAction,
  ThreadDropZones,
} from "@/features/agents/components/ThreadDropZones"

export interface ThreadAction {
  threadId: string
  action: "pin" | "unpin" | "archive" | "unarchive"
}

export function ThreadDragDrop({
  children,
  onAction,
  pinnedSectionRef,
}: {
  children: ReactNode
  onAction: (actions: Array<ThreadAction>) => void
  pinnedSectionRef?: RefObject<HTMLElement | null>
}) {
  const [container, setContainer] = useState<HTMLDivElement | null>(null)
  const nativeDragImage = useRef<HTMLCanvasElement>(null)
  const selection = useSidebarThreadSelection()
  const appearances = useRef(new Map<string, ReactNode>())

  const registerAppearance = useCallback((id: string, content: ReactNode) => {
    if (content === null) appearances.current.delete(id)
    else appearances.current.set(id, content)
  }, [])

  const [preview, setPreview] = useState<{
    row: HTMLElement
    y: number
    items: Array<ThreadDragItem>
  } | null>(null)

  const [dropAction, setDropAction] = useState<ThreadDropAction | null>(null)
  const [threadId, setThreadId] = useState<string | null>(null)

  const pinned = preview?.items.some((item) => item.pinned) ?? false
  const archived = preview?.items.some((item) => item.archived) ?? false

  return (
    <SidebarThreadDragPreviewContext value={registerAppearance}>
      <canvas
        ref={nativeDragImage}
        width={1}
        height={1}
        aria-hidden="true"
        className="pointer-events-none fixed top-0 left-0"
      />
      {preview && container && (
        <ThreadDragPreview
          key={preview.row.dataset.selectableThread}
          {...preview}
          selected={threadId !== null && dropAction !== null}
          container={container}
          onFinish={() => setPreview(null)}
        />
      )}
      <div
        className="relative flex min-h-0 flex-1 flex-col"
        onDragStart={(event) => {
          if (event.defaultPrevented || !(event.target instanceof Element)) {
            return
          }

          const row = event.target.closest<HTMLElement>(
            "[data-selectable-thread]"
          )
          const id = row?.dataset.selectableThread
          if (!id || !row) return

          // Disable default drag image served by browser
          if (nativeDragImage.current) {
            event.dataTransfer.setDragImage(nativeDragImage.current, 0, 0)
          }

          const selected = selection?.selected
          const rows = selected?.has(id)
            ? Array.from(
                event.currentTarget.querySelectorAll<HTMLElement>(
                  "[data-selectable-thread]"
                )
              ).filter((entry) =>
                selected.has(entry.dataset.selectableThread ?? "")
              )
            : [row]

          const items = new Map<string, ThreadDragItem>()
          for (const entry of rows) {
            const entryId = entry.dataset.selectableThread
            if (!entryId || items.has(entryId)) continue
            items.set(entryId, {
              id: entryId,
              row: entry,
              content:
                appearances.current.get(entryId) ?? entry.dataset.threadTitle,
              pinned: entry.dataset.threadPinned === "true",
              archived: entry.dataset.threadArchived === "true",
            })
          }

          // Paint farther rows first so the closest row sits on top.
          const orderedItems = [...items.values()]
            .map((item) => {
              const bounds = item.row.getBoundingClientRect()
              const distance = Math.max(
                bounds.top - event.clientY,
                event.clientY - bounds.bottom,
                0
              )
              const centerDistance = Math.abs(
                bounds.top + bounds.height / 2 - event.clientY
              )

              return { item, distance, centerDistance }
            })
            .sort((a, b) => {
              const distance = b.distance - a.distance
              const center = b.centerDistance - a.centerDistance
              return distance || center
            })
            .map((entry) => entry.item)

          setPreview({ row, y: event.clientY, items: orderedItems })
          event.dataTransfer.effectAllowed = "move"
          event.dataTransfer.setData("text/plain", id)

          setThreadId(id)
        }}
        onDragEnd={() => {
          setThreadId(null)
          setDropAction(null)
        }}
        onDrop={() => {
          setThreadId(null)
          setDropAction(null)
        }}
      >
        <ThreadDropZones
          pinnedSectionRef={pinnedSectionRef}
          active={threadId !== null}
          count={preview?.items.length ?? 0}
          pinned={pinned}
          archived={archived}
          dropAction={dropAction}
          onHoverChange={setDropAction}
          onContainer={setContainer}
          onAction={(action) => {
            if (!threadId) return
            const actions: Array<ThreadAction> = []
            for (const item of preview?.items ?? []) {
              if (action === "restore") {
                if (item.pinned) {
                  actions.push({ threadId: item.id, action: "unpin" })
                }
                if (item.archived) {
                  actions.push({ threadId: item.id, action: "unarchive" })
                }
              } else {
                actions.push({ threadId: item.id, action })
              }
            }

            if (actions.length) onAction(actions)
          }}
        />
        {children}
      </div>
    </SidebarThreadDragPreviewContext>
  )
}
