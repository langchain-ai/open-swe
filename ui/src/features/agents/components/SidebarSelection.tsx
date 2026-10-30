import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { PushPinRegularIcon } from "@langchain/macaw-components/icons"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { ArchiveIcon } from "@phosphor-icons/react/dist/ssr/Archive"
import { ArrowCounterClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowCounterClockwise"
import { CopyIcon } from "@phosphor-icons/react/dist/ssr/Copy"
import { DotsThreeIcon } from "@phosphor-icons/react/dist/ssr/DotsThree"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { LinkSimpleIcon } from "@phosphor-icons/react/dist/ssr/LinkSimple"
import { PushPinSlashIcon } from "@phosphor-icons/react/dist/ssr/PushPinSlash"
import { SparkleIcon } from "@phosphor-icons/react/dist/ssr/Sparkle"
import { TrashIcon } from "@phosphor-icons/react/dist/ssr/Trash"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"
import { createContext, useContext, useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { ContextMenuItem, DropdownMenuItem } from "@/components/MenuItem"
import { DeleteThreadDialog } from "@/features/agents/components/DeleteThreadDialog"
import type { ThreadMenuKind } from "@/features/agents/components/ThreadMenuItems"
import {
  useDeleteAgentThread,
  usePinAgentThread,
  useRegenerateAgentThreadTitle,
  useResolveAgentThread,
  useSidebarPinnedThreads,
} from "@/features/agents/lib/queries"
import type { SidebarThreadItem } from "@/features/agents/lib/sidebarThreads"
import { threadPullRequest } from "@/features/agents/lib/threadPullRequest"
import { reviewPagePath } from "@/features/reviews/lib/reviewEntry"
import { copyAppLinks, copyText } from "@/lib/clipboard"
import { useAltKey } from "@/lib/useAltKey"

const ICON_SIZE = 14

/** Marks every selectable row so a Shift-click can select the rows between two in screen order. */
export const SELECTABLE_ROW_ATTRIBUTE = "data-sidebar-thread-key"

interface SidebarSelection {
  selected: ReadonlyMap<string, SidebarThreadItem>
  /** The selected threads as their rows last rendered them, so a pin or archive since is reflected. */
  items: () => Array<SidebarThreadItem>
  isPinned: (item: SidebarThreadItem) => boolean
  /** Ctrl/Cmd-click toggles a row, Shift-click extends from the last one; returns whether it handled the click. */
  handleClick: (event: React.MouseEvent, item: SidebarThreadItem) => boolean
  register: (item: SidebarThreadItem) => () => void
  clear: () => void
  deleteOpen: boolean
  setDeleteOpen: (open: boolean) => void
}

/** Escape belongs to an open menu or dialog first. */
const OPEN_LAYER =
  '[role="dialog"][data-state="open"], [role="menu"][data-state="open"]'

const SidebarSelectionContext = createContext<SidebarSelection | null>(null)

export function useSidebarSelection(): SidebarSelection | null {
  return useContext(SidebarSelectionContext)
}

export function SidebarSelectionProvider({
  activeThreadId,
  children,
}: {
  /** Where a Shift-click extends from before anything is selected. */
  activeThreadId?: string
  children: React.ReactNode
}) {
  const pinnedThreads = useSidebarPinnedThreads()
  const isPinned = (item: SidebarThreadItem) =>
    Boolean(pinnedThreads.data?.some((thread) => thread.id === item.id))
  const anchorKey = activeThreadId ? `cloud:${activeThreadId}` : undefined
  const [selected, setSelected] = useState<
    ReadonlyMap<string, SidebarThreadItem>
  >(new Map())
  const [deleteOpen, setDeleteOpen] = useState(false)
  const anchor = useRef<string | undefined>(undefined)
  const rows = useRef(new Map<string, SidebarThreadItem>())

  const clear = () => {
    anchor.current = undefined
    setSelected(new Map())
  }

  useEffect(() => {
    if (selected.size === 0) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return
      if (document.querySelector(OPEN_LAYER)) return
      anchor.current = undefined
      setSelected(new Map())
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [selected.size])

  const rowKeysInOrder = () =>
    Array.from(
      document.querySelectorAll<HTMLElement>(`[${SELECTABLE_ROW_ATTRIBUTE}]`),
      (row) => row.getAttribute(SELECTABLE_ROW_ATTRIBUTE) ?? ""
    )

  const handleClick = (event: React.MouseEvent, item: SidebarThreadItem) => {
    if (event.shiftKey) {
      const keys = rowKeysInOrder()
      const from = keys.indexOf(anchor.current ?? anchorKey ?? item.key)
      const to = keys.indexOf(item.key)
      const range =
        from < 0 || to < 0
          ? [item.key]
          : keys.slice(Math.min(from, to), Math.max(from, to) + 1)
      setSelected((current) => {
        const next = new Map(current)
        for (const key of range) {
          const row = rows.current.get(key)
          if (row) next.set(key, row)
        }
        return next
      })
      anchor.current ??= item.key
      return true
    }
    if (event.metaKey || event.ctrlKey) {
      setSelected((current) => {
        const next = new Map(current)
        if (next.has(item.key)) next.delete(item.key)
        else next.set(item.key, item)
        return next
      })
      anchor.current = item.key
      return true
    }
    if (selected.size > 0) clear()
    return false
  }

  const items = () =>
    [...selected].map(([key, item]) => rows.current.get(key) ?? item)

  const register = (item: SidebarThreadItem) => {
    rows.current.set(item.key, item)
    return () => {
      if (rows.current.get(item.key) === item) rows.current.delete(item.key)
    }
  }

  return (
    <SidebarSelectionContext.Provider
      value={{
        selected,
        items,
        isPinned,
        handleClick,
        register,
        clear,
        deleteOpen,
        setDeleteOpen,
      }}
    >
      {children}
    </SidebarSelectionContext.Provider>
  )
}

const MENU_ITEM = {
  dropdown: DropdownMenuItem,
  context: ContextMenuItem,
} as const

/** Every action the thread menu offers that makes sense for many threads at once. */
export function SidebarBulkMenuItems({ menu }: { menu: ThreadMenuKind }) {
  const selection = useSidebarSelection()
  const pinThread = usePinAgentThread()
  const resolveThread = useResolveAgentThread()
  const regenerateTitle = useRegenerateAgentThreadTitle()
  const github = useAltKey()
  if (!selection) return null
  const Item = MENU_ITEM[menu]
  const items = selection.items()
  const allPinned = items.every(selection.isPinned)
  const allArchived = items.every((item) => item.thread.resolved === true)
  const pullRequests = items.flatMap((item) => {
    const pullRequest = threadPullRequest(item.thread)
    return pullRequest ? [pullRequest] : []
  })

  const pinAll = () => {
    for (const item of items) {
      if (selection.isPinned(item) === allPinned)
        pinThread.mutate({ threadId: item.id, pinned: !allPinned })
    }
  }
  const archiveAll = () => {
    for (const item of items) {
      if ((item.thread.resolved === true) === allArchived)
        resolveThread.mutate({ threadId: item.id, resolved: !allArchived })
    }
  }
  const regenerateAll = () => {
    void Promise.allSettled(
      items.map((item) => regenerateTitle.mutateAsync({ threadId: item.id }))
    ).then((results) => {
      const done = results.filter(
        (result) => result.status === "fulfilled"
      ).length
      if (done > 0)
        toast.success(
          done === 1 ? "Regenerated 1 title" : `Regenerated ${done} titles`
        )
    })
  }
  const copyPullRequests = () => {
    const noun = pullRequests.length === 1 ? "link" : "links"
    if (github)
      copyText(
        pullRequests.map((pullRequest) => pullRequest.url).join("\n"),
        `Copied ${pullRequests.length} GitHub ${noun}`
      )
    else
      copyAppLinks(
        pullRequests.map((pullRequest) => reviewPagePath(pullRequest.review)),
        `Copied ${pullRequests.length} review ${noun}`
      )
  }

  return (
    <>
      <Item onSelect={pinAll}>
        {allPinned ? (
          <PushPinSlashIcon size={ICON_SIZE} weight="regular" />
        ) : (
          <PushPinRegularIcon size={ICON_SIZE} />
        )}
        {allPinned ? "Unpin threads" : "Pin threads"}
      </Item>
      <Item onSelect={archiveAll}>
        {allArchived ? (
          <ArrowCounterClockwiseIcon size={ICON_SIZE} weight="regular" />
        ) : (
          <ArchiveIcon size={ICON_SIZE} weight="regular" />
        )}
        {allArchived ? "Unarchive threads" : "Archive threads"}
      </Item>
      <Item onSelect={regenerateAll}>
        <SparkleIcon size={ICON_SIZE} weight="regular" />
        Regenerate titles
      </Item>
      {pullRequests.length > 0 && (
        <Item
          onSelect={copyPullRequests}
          title="Hold ⌥ Option / Alt to copy GitHub links"
        >
          {github ? (
            <GithubLogoIcon size={ICON_SIZE} weight="regular" />
          ) : (
            <LinkSimpleIcon size={ICON_SIZE} weight="regular" />
          )}
          {github ? "Copy GitHub PR links" : "Copy PR review links"}
        </Item>
      )}
      <Item
        onSelect={() =>
          copyText(
            items.map((item) => item.id).join("\n"),
            items.length === 1
              ? "Copied 1 thread ID"
              : `Copied ${items.length} thread IDs`
          )
        }
      >
        <CopyIcon size={ICON_SIZE} weight="regular" />
        Copy thread IDs
      </Item>
      <Item
        className="text-error-secondary"
        onSelect={() => selection.setDeleteOpen(true)}
      >
        <TrashIcon size={ICON_SIZE} weight="regular" />
        Delete threads…
      </Item>
    </>
  )
}

/** Sits above the account menu while rows are selected: the count, the bulk actions, and a way out. */
export function SidebarSelectionBar() {
  const selection = useSidebarSelection()
  const deleteThread = useDeleteAgentThread()
  const [deleting, setDeleting] = useState(false)
  if (!selection || selection.selected.size === 0) return null
  const items = selection.items()

  const deleteAll = () => {
    if (deleting) return
    setDeleting(true)
    void Promise.allSettled(
      items.map((item) => deleteThread.mutateAsync(item.id))
    ).then(() => {
      setDeleting(false)
      selection.setDeleteOpen(false)
      selection.clear()
    })
  }

  return (
    <div
      role="toolbar"
      aria-label="Selected threads"
      className="mx-space-2 mb-space-1 flex items-center gap-space-1 rounded-lg border border-default bg-surface-level-1 py-space-1 pr-space-1 pl-space-3"
    >
      <span className="min-w-0 flex-1 truncate text-xs font-medium text-primary">
        {items.length} selected
      </span>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <IconButton
            icon={DotsThreeIcon}
            iconWeight="bold"
            label="Actions for selected threads"
            size="xs"
            color="secondary"
            variant="plain"
          />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" side="top" className="min-w-[10rem]">
          <SidebarBulkMenuItems menu="dropdown" />
        </DropdownMenuContent>
      </DropdownMenu>
      <IconButton
        icon={XIcon}
        label="Clear selection"
        size="xs"
        color="secondary"
        variant="plain"
        onClick={selection.clear}
      />
      <DeleteThreadDialog
        open={selection.deleteOpen}
        onOpenChange={selection.setDeleteOpen}
        threadTitle={items[0]?.title ?? ""}
        count={items.length}
        isDeleting={deleting}
        onConfirm={deleteAll}
      />
    </div>
  )
}
