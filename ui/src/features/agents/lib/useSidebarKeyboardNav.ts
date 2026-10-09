import { useEffect, useMemo, useRef } from "react"

import type { AppCommand } from "@/lib/appCommands"
import { useRegisterAppCommands } from "@/lib/appCommands"

const ROW_SELECTOR = "a[data-sidebar-thread]"

type Step = 1 | -1

/** The thread rows a list currently renders, in on-screen order. */
class ThreadRows {
  private readonly rows: Array<HTMLAnchorElement>

  constructor(
    root: HTMLElement | null,
    private readonly activeKey: string | undefined
  ) {
    this.rows = Array.from(
      root?.querySelectorAll<HTMLAnchorElement>(ROW_SELECTOR) ?? []
    )
  }

  current(): HTMLAnchorElement | undefined {
    return (
      this.rows.find((row) => row.dataset.sidebarThread === this.activeKey) ??
      this.rows.find((row) => row === document.activeElement)
    )
  }

  adjacent(step: Step, from: Element | undefined = this.current()) {
    const index = from ? this.rows.indexOf(from as HTMLAnchorElement) : -1
    if (index === -1) return step === 1 ? this.rows[0] : this.rows.at(-1)
    return this.rows[index + step]
  }
}

function openRow(row: HTMLAnchorElement | undefined) {
  if (!row) return
  row.focus({ preventScroll: true })
  row.scrollIntoView({ block: "nearest" })
  row.click()
}

/**
 * Inbox-style triage over the sidebar's thread rows: J/K (or the arrow keys
 * while a row has focus) open the next or previous thread, and E archives the
 * open thread and moves on. Rows are read from the DOM so every organize mode
 * and collapsed folder is walked in the order it is shown.
 */
export function useSidebarKeyboardNav({
  viewport,
  activeKey,
  archiveActive,
}: {
  viewport: React.RefObject<HTMLElement | null>
  activeKey: string | undefined
  archiveActive: () => void
}) {
  const latest = useRef({ viewport, activeKey, archiveActive })
  useEffect(() => {
    latest.current = { viewport, activeKey, archiveActive }
  })

  const commands = useMemo<ReadonlyArray<AppCommand>>(() => {
    const rows = () =>
      new ThreadRows(latest.current.viewport.current, latest.current.activeKey)
    return [
      {
        id: "next-thread",
        label: "Next thread",
        aliases: ["down", "inbox"],
        shortcuts: ["j"],
        group: "Thread",
        run: () => openRow(rows().adjacent(1)),
      },
      {
        id: "previous-thread",
        label: "Previous thread",
        aliases: ["up", "inbox"],
        shortcuts: ["k"],
        group: "Thread",
        run: () => openRow(rows().adjacent(-1)),
      },
      {
        id: "archive-and-next-thread",
        label: "Archive and open next thread",
        aliases: ["done", "mark done", "triage"],
        shortcuts: ["e"],
        group: "Thread",
        run: () => {
          const list = rows()
          const current = list.current()
          const next = current
            ? (list.adjacent(1, current) ?? list.adjacent(-1, current))
            : undefined
          latest.current.archiveActive()
          openRow(next)
        },
      },
    ]
  }, [])
  useRegisterAppCommands(commands)

  return (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return
    const step: Step | null =
      event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : null
    if (!step || !(event.target instanceof Element)) return
    const row = event.target.closest(ROW_SELECTOR) ?? undefined
    if (!row) return
    event.preventDefault()
    openRow(new ThreadRows(viewport.current, activeKey).adjacent(step, row))
  }
}
