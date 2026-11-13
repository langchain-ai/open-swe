import { PushPinRegularIcon } from "@langchain/macaw-components/icons"
import { ArchiveIcon } from "@phosphor-icons/react/dist/ssr/Archive"
import { PushPinSlashIcon } from "@phosphor-icons/react/dist/ssr/PushPinSlash"
import { useCallback, useLayoutEffect, useRef, useState } from "react"
import type { RefObject } from "react"

import { cn } from "@/lib/utils"

export type ThreadDropAction = "pin" | "archive" | "restore"

const DROP_ZONES = {
  pin: { icon: PushPinRegularIcon, label: "pin" },
  archive: { icon: ArchiveIcon, label: "archive" },
  unpin: { icon: PushPinSlashIcon, label: "unpin" },
  unarchive: { icon: PushPinSlashIcon, label: "unarchive" },
  restore: { icon: PushPinSlashIcon, label: "unpin and unarchive" },
} as const

export function ThreadDropZones(props: {
  active: boolean
  count: number
  pinned: boolean
  archived: boolean
  pinnedSectionRef?: RefObject<HTMLElement | null>
  onContainer: (container: HTMLDivElement | null) => void
  onAction: (action: ThreadDropAction) => void
  dropAction: ThreadDropAction | null
  onHoverChange: (action: ThreadDropAction | null) => void
}) {
  const { onContainer, onAction, onHoverChange } = props
  const [lastThread, setLastThread] = useState({
    pinned: props.pinned,
    archived: props.archived,
    count: props.count,
  })
  if (
    props.active &&
    (lastThread.pinned !== props.pinned ||
      lastThread.archived !== props.archived ||
      lastThread.count !== props.count)
  ) {
    setLastThread({
      pinned: props.pinned,
      archived: props.archived,
      count: props.count,
    })
  }

  const showPinned = props.active ? props.pinned : lastThread.pinned
  const showArchived = props.active ? props.archived : lastThread.archived
  const count = props.active ? props.count : lastThread.count
  const singlePinned = showPinned && count === 1

  const rootRef = useRef<HTMLDivElement>(null)
  const archiveRef = useRef<HTMLDivElement>(null)
  const [unpinBounds, setUnpinBounds] = useState({ top: 0, height: 0 })
  const attach = useCallback(
    (element: HTMLDivElement | null) => {
      rootRef.current = element
      onContainer(element)
    },
    [onContainer]
  )

  // Keep unpin between the scrolling Pinned section and the fixed archive zone.
  useLayoutEffect(() => {
    const root = rootRef.current
    if (!root || !props.active || !singlePinned) return

    const section = props.pinnedSectionRef?.current
    const archive = archiveRef.current

    const clamp = (value: number, min: number, max: number) =>
      Math.max(min, Math.min(value, max))

    const measure = () => {
      const rootRect = root.getBoundingClientRect()
      const sectionRect = section?.getBoundingClientRect()
      const archiveRect = archive?.getBoundingClientRect()

      const top = clamp(
        sectionRect ? sectionRect.bottom - rootRect.top : 0,
        0,
        rootRect.height
      )

      const bottom = archiveRect?.top ?? rootRect.bottom
      const height = Math.max(0, bottom - rootRect.top - top)

      setUnpinBounds((prev) => {
        if (prev.top === top && prev.height === height) return prev
        return { top, height }
      })
    }

    measure()
    const observer = new ResizeObserver(measure)

    observer.observe(root)
    if (section) observer.observe(section)
    if (archive) observer.observe(archive)

    window.addEventListener("scroll", measure, { capture: true, passive: true })
    window.addEventListener("resize", measure)
    return () => {
      observer.disconnect()
      window.removeEventListener("scroll", measure, true)
      window.removeEventListener("resize", measure)
    }
  }, [props.active, singlePinned, props.pinnedSectionRef])

  const zones = {
    pin: DROP_ZONES.pin,
    archive: DROP_ZONES.archive,
    restore: showPinned
      ? showArchived
        ? DROP_ZONES.restore
        : DROP_ZONES.unpin
      : DROP_ZONES.unarchive,
  }

  return (
    <div ref={attach} className="pointer-events-none absolute inset-0">
      {(
        [
          ...(singlePinned ? [] : ["pin" as const]),
          ...(showPinned || showArchived ? ["restore" as const] : []),
          "archive",
        ] as const
      ).map((action) => {
        const { icon: ZoneIcon, label: actionLabel } = zones[action]
        const label = `Drag to ${actionLabel}`
        return (
          <div
            key={action}
            ref={action === "archive" ? archiveRef : undefined}
            role="region"
            aria-hidden={!props.active}
            inert={!props.active}
            aria-label={label}
            style={
              singlePinned && action === "restore" ? unpinBounds : undefined
            }
            className={cn(
              "absolute inset-x-0 z-floating-bar flex items-center justify-center text-sm text-primary",
              props.active && "pointer-events-auto",
              action === "restore"
                ? singlePinned
                  ? "overflow-hidden"
                  : "top-30 bottom-30"
                : "h-30",
              action === "pin" && "top-0",
              action === "archive" && "bottom-0"
            )}
            onDragOver={(event) => {
              event.preventDefault()
              event.stopPropagation()
              event.dataTransfer.dropEffect = "move"
              onHoverChange(action)
            }}
            onDragLeave={() => onHoverChange(null)}
            onDrop={(event) => {
              event.preventDefault()
              onAction(action)
            }}
          >
            <span
              aria-hidden="true"
              className={cn(
                "pointer-events-none absolute inset-0 from-surface-level-2 via-surface-level-2/90 to-transparent transition-opacity duration-normal motion-reduce:transition-none",
                props.active ? "opacity-100 starting:opacity-0" : "opacity-0",
                action === "restore"
                  ? "bg-linear-to-b from-transparent via-surface-level-2/90 to-transparent"
                  : action === "pin"
                    ? "bg-linear-to-b"
                    : "bg-linear-to-t"
              )}
            />
            <span
              aria-hidden="true"
              className={cn(
                "pointer-events-none absolute inset-0 from-brand/25 to-transparent transition-opacity duration-normal motion-reduce:transition-none",
                action === "restore"
                  ? "bg-linear-to-b from-transparent via-brand/25 to-transparent"
                  : action === "pin"
                    ? "bg-linear-to-b"
                    : "bg-linear-to-t",
                props.active && props.dropAction === action
                  ? "opacity-100"
                  : "opacity-0"
              )}
            />
            <span
              className={cn(
                "pointer-events-none relative z-dashboard-header flex items-center gap-space-2 rounded-full bg-elevated px-space-3 py-space-2 shadow-sm transition-all duration-normal motion-reduce:transition-none",
                !props.active
                  ? "opacity-0"
                  : props.dropAction === action
                    ? "translate-y-0 opacity-100"
                    : "opacity-100 starting:opacity-0",
                props.dropAction !== action &&
                  (action === "pin" ? "translate-y-1" : "-translate-y-1")
              )}
            >
              <ZoneIcon size={16} weight="regular" aria-hidden="true" />
              {label}
            </span>
          </div>
        )
      })}
    </div>
  )
}
