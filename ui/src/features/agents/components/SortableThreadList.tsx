import { useId, useState } from "react"
import type { ReactNode } from "react"

import { useSidebarThreadSelection } from "@/features/agents/components/SidebarThreadSelection"
import type { SidebarThreadItem } from "@/features/agents/lib/sidebarThreads"
import { cn } from "@/lib/utils"

export function SortableThreadList({
  items,
  threadIds,
  disabled,
  onReorder,
  renderRow,
  label = "threads",
}: {
  items: Array<SidebarThreadItem>
  threadIds: Array<string>
  disabled: boolean
  onReorder: (ids: Array<string>) => void
  renderRow: (item: SidebarThreadItem) => ReactNode
  label?: string
}) {
  const selection = useSidebarThreadSelection()
  const helpId = useId()
  const [draggedId, setDraggedId] = useState<string | null>(null)
  const [target, setTarget] = useState<{ id: string; after: boolean } | null>(
    null
  )
  const [announcement, setAnnouncement] = useState("")

  const move = (id: string, targetId: string, after: boolean) => {
    if (disabled || id === targetId) return
    const ids = threadIds.filter((entry) => entry !== id)
    const index = ids.indexOf(targetId)
    if (index < 0 || !threadIds.includes(id)) return
    ids.splice(index + Number(after), 0, id)
    if (ids.every((entry, position) => entry === threadIds[position])) return
    onReorder(ids)
    const title = items.find((item) => item.id === id)?.title ?? "Thread"
    setAnnouncement(
      `${title} moved to position ${ids.indexOf(id) + 1} of ${ids.length}.`
    )
  }

  return (
    <>
      <span className="sr-only" aria-live="polite">
        {announcement}
      </span>
      <span id={helpId} className="sr-only">
        Drag to reorder {label}, or press Alt and the up or down arrow while a
        thread is focused.
      </span>
      {items.map((item, index) => (
        <div
          key={item.key}
          draggable={!disabled && items.length > 1}
          aria-describedby={helpId}
          className={cn("relative", draggedId === item.id && "opacity-50")}
          onDragStart={(event) => {
            if (
              disabled ||
              (event.target !== event.currentTarget &&
                (!(event.target instanceof Element) ||
                  event.target.closest("a") !==
                    event.currentTarget.querySelector("a")))
            ) {
              event.preventDefault()
              return
            }

            if (
              selection &&
              selection.selected.size > 1 &&
              selection.selected.has(item.id)
            ) {
              return
            }
            event.dataTransfer.effectAllowed = "move"
            event.dataTransfer.setData("text/plain", item.id)
            setDraggedId(item.id)
          }}
          onDragOver={(event) => {
            if (!draggedId || disabled) return
            if (draggedId === item.id) {
              setTarget(null)
              return
            }
            event.preventDefault()
            event.dataTransfer.dropEffect = "move"
            const bounds = event.currentTarget.getBoundingClientRect()
            setTarget({
              id: item.id,
              after: event.clientY >= bounds.top + bounds.height / 2,
            })
          }}
          onDragLeave={(event) => {
            if (event.relatedTarget instanceof Node) {
              if (event.currentTarget.contains(event.relatedTarget)) return
            } else {
              const bounds = event.currentTarget.getBoundingClientRect()
              if (
                event.clientX >= bounds.left &&
                event.clientX < bounds.right &&
                event.clientY >= bounds.top &&
                event.clientY < bounds.bottom
              )
                return
            }
            setTarget(null)
          }}
          onDrop={(event) => {
            if (!draggedId || !target || target.id !== item.id || disabled)
              return
            event.preventDefault()
            move(draggedId, target.id, target.after)
            setDraggedId(null)
            setTarget(null)
          }}
          onDragEnd={() => {
            setDraggedId(null)
            setTarget(null)
          }}
          onKeyDown={(event) => {
            if (
              !event.altKey ||
              disabled ||
              !(event.target instanceof Element) ||
              event.target.closest("a") !==
                event.currentTarget.querySelector("a") ||
              (event.key !== "ArrowUp" && event.key !== "ArrowDown")
            )
              return
            const neighbor = items[index + (event.key === "ArrowUp" ? -1 : 1)]
            if (!neighbor) return
            event.preventDefault()
            event.stopPropagation()
            move(item.id, neighbor.id, event.key === "ArrowDown")
          }}
        >
          {renderRow(item)}
          {target?.id === item.id && !disabled && (
            <div
              aria-hidden="true"
              className={cn(
                "pointer-events-none absolute inset-x-0 z-floating-bar mx-space-2 flex h-0 items-center",
                target.after ? "bottom-0" : "top-0"
              )}
            >
              <span className="size-2 shrink-0 rounded-full border-2 border-brand bg-surface-level-2" />
              <span className="h-0.5 flex-1 rounded-full bg-brand" />
            </div>
          )}
        </div>
      ))}
    </>
  )
}
