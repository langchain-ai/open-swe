import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { ArchiveIcon } from "@phosphor-icons/react/dist/ssr/Archive"
import { TrashIcon } from "@phosphor-icons/react/dist/ssr/Trash"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"
import { useIsFetching, useQueryClient } from "@tanstack/react-query"
import { createContext, useContext, useEffect, useRef, useState } from "react"
import type { MouseEvent, ReactNode } from "react"

import {
  agentThreadKeys,
  useDeleteAgentThread,
  useResolveAgentThread,
} from "@/features/agents/lib/queries"
import type { SidebarThreadItem } from "@/features/agents/lib/sidebarThreads"

const SelectionContext = createContext<{
  selected: ReadonlyMap<string, string>
  click: (
    event: MouseEvent<HTMLAnchorElement>,
    item: SidebarThreadItem
  ) => boolean
} | null>(null)

export function useSidebarThreadSelection() {
  return useContext(SelectionContext)
}

export function SidebarThreadSelection({
  children,
  newThreadControl,
}: {
  children: ReactNode
  newThreadControl: ReactNode
}) {
  const root = useRef<HTMLDivElement>(null)
  const anchor = useRef<string | null>(null)
  const [selected, setSelected] = useState(new Map<string, string>())
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const queryClient = useQueryClient()
  const fetching = useIsFetching({ queryKey: agentThreadKeys.lists })
  const archive = useResolveAgentThread()
  const remove = useDeleteAgentThread()

  useEffect(() => {
    if (!root.current || busy || deleteOpen || fetching) return
    const prune = () => {
      const visible = new Set(
        Array.from(
          root.current?.querySelectorAll<HTMLElement>(
            "[data-selectable-thread]"
          ) ?? [],
          (row) => row.dataset.selectableThread
        )
      )
      setSelected((current) => {
        if ([...current.keys()].every((id) => visible.has(id))) return current
        return new Map([...current].filter(([id]) => visible.has(id)))
      })
    }
    prune()
    const observer = new MutationObserver(prune)
    observer.observe(root.current, { childList: true, subtree: true })
    return () => observer.disconnect()
  }, [busy, deleteOpen, fetching])

  const clear = () => {
    setSelected(new Map())
    anchor.current = null
  }
  const click = (
    event: MouseEvent<HTMLAnchorElement>,
    item: SidebarThreadItem
  ) => {
    if (busy || deleteOpen) {
      event.preventDefault()
      return true
    }
    if (!event.shiftKey && !event.metaKey && !event.ctrlKey) {
      clear()
      anchor.current = item.id
      return false
    }
    event.preventDefault()
    const next = new Map(selected)
    if (event.shiftKey) {
      const rows = Array.from(
        root.current?.querySelectorAll<HTMLAnchorElement>(
          "a[data-selectable-thread]"
        ) ?? []
      )
      const start = rows.findIndex(
        (row) => row.dataset.selectableThread === anchor.current
      )
      const end = rows.findIndex(
        (row) => row.dataset.selectableThread === item.id
      )
      const range =
        start < 0 || end < 0
          ? []
          : rows.slice(Math.min(start, end), Math.max(start, end) + 1)
      if (!event.metaKey && !event.ctrlKey) next.clear()
      for (const row of range) {
        const id = row.dataset.selectableThread
        const title = row.dataset.threadTitle
        if (id && title) next.set(id, title)
      }
      next.set(item.id, item.title)
      if (!anchor.current) anchor.current = item.id
    } else {
      if (next.has(item.id)) next.delete(item.id)
      else next.set(item.id, item.title)
      anchor.current = item.id
    }
    setSelected(next)
    return true
  }
  const countLabel = `${selected.size} ${selected.size === 1 ? "thread" : "threads"}`
  const apply = async (action: "archive" | "delete") => {
    if (busy) return
    setBusy(true)
    const remaining = new Map(selected)
    for (const id of selected.keys()) {
      try {
        if (action === "archive")
          await archive.mutateAsync({ threadId: id, resolved: true })
        else await remove.mutateAsync(id)
        remaining.delete(id)
      } catch {
        remaining.set(id, selected.get(id) ?? "Thread")
      }
    }
    await queryClient.refetchQueries({ queryKey: agentThreadKeys.lists })
    setSelected(remaining)
    setBusy(false)
    setDeleteOpen(false)
    if (!remaining.size) anchor.current = null
  }

  return (
    <SelectionContext value={{ selected, click }}>
      <div
        ref={root}
        className="flex min-h-0 flex-1 flex-col"
        onKeyDown={(event) => {
          if (event.key === "Escape" && !busy && !deleteOpen) clear()
        }}
      >
        <div className="relative shrink-0">
          <div
            className={selected.size > 0 ? "invisible" : undefined}
            inert={selected.size > 0}
          >
            {newThreadControl}
          </div>
          {selected.size > 0 && (
            <div className="absolute inset-0 flex items-center gap-space-1 bg-surface-level-2 px-space-4 pb-space-1">
              <span
                className="mr-auto text-xs whitespace-nowrap text-secondary"
                role="status"
              >
                {selected.size} selected
              </span>
              <IconButton
                icon={ArchiveIcon}
                label="Archive"
                size="xs"
                color="secondary"
                variant="plain"
                disabled={busy}
                className="disabled:bg-transparent"
                onClick={() => void apply("archive")}
              />
              <IconButton
                icon={TrashIcon}
                label="Delete"
                size="xs"
                color="error"
                variant="plain"
                disabled={busy}
                className="disabled:bg-transparent"
                onClick={() => setDeleteOpen(true)}
              />
              <IconButton
                icon={XIcon}
                label="Clear"
                size="xs"
                color="secondary"
                variant="plain"
                disabled={busy}
                className="disabled:bg-transparent"
                onClick={clear}
              />
            </div>
          )}
        </div>
        {children}
        <Dialog
          open={deleteOpen}
          onOpenChange={(open) => {
            if (!busy) setDeleteOpen(open)
          }}
        >
          <DialogContent
            title={`Delete ${countLabel}`}
            description={`Permanently delete ${countLabel}? This cannot be undone.`}
            showClose={false}
            className="w-[min(28rem,calc(100vw-2rem))]"
          >
            <div className="flex justify-end gap-space-2">
              <Button
                size="xs"
                color="secondary"
                variant="outlined"
                disabled={busy}
                onClick={() => setDeleteOpen(false)}
              >
                Cancel
              </Button>
              <Button
                size="xs"
                color="error"
                disabled={busy}
                onClick={() => void apply("delete")}
              >
                {busy ? "Deleting…" : `Delete ${countLabel}`}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </div>
    </SelectionContext>
  )
}
