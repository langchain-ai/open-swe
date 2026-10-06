import { useMemo, useState } from "react"
import {
  ArrowsClockwiseIcon,
  CaretDownIcon,
  FolderIcon,
} from "@phosphor-icons/react"

import { Popover, PopoverContent, PopoverTrigger } from "@langchain/gtm-platform-design-system/ui/popover"
import { useRefreshRepos } from "@/lib/profile"
import { cn } from "@/lib/utils"

type RepoOption = { full_name: string; private?: boolean; archived?: boolean }

interface RepoSelectorProps {
  repos?: Array<RepoOption>
  selectedRepo?: string | null
  selectedLabel?: string
  onRepoChange: (repo: string | null) => void
  placeholder?: string
  emptySelectionLabel?: string
  searchPlaceholder?: string
  noMatchesLabel?: string
  className?: string
  triggerClassName?: string
  dropdownClassName?: string
  side?: "top" | "bottom"
  disabled?: boolean
  allowArchived?: boolean
}

export function RepoSelector({
  repos,
  selectedRepo = null,
  selectedLabel,
  onRepoChange,
  placeholder = "Select repository",
  emptySelectionLabel = "No repository",
  searchPlaceholder = "Search repositories…",
  noMatchesLabel = "No matches",
  className,
  triggerClassName,
  dropdownClassName,
  side = "bottom",
  disabled = false,
  allowArchived = false,
}: RepoSelectorProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [showArchived, setShowArchived] = useState(false)
  const refresh = useRefreshRepos()

  const filteredRepos = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (repos ?? []).filter(
      (repo) =>
        ((allowArchived && showArchived) || !repo.archived) &&
        repo.full_name.toLowerCase().includes(q)
    )
  }, [repos, query, showArchived, allowArchived])

  return (
    <Popover
      open={open}
      onOpenChange={(value) => {
        setOpen(value)
        if (!value) setQuery("")
      }}
    >
      <div className={cn("min-w-0 shrink", className)}>
        <PopoverTrigger
          render={
            <button
              type="button"
              disabled={disabled}
              title={selectedRepo ?? undefined}
              className={cn(
                "flex max-w-[260px] cursor-pointer items-center gap-1 text-ink-subtle transition-opacity hover:opacity-80 disabled:cursor-default disabled:opacity-60",
                triggerClassName
              )}
            />
          }
        >
          <FolderIcon className="size-3.5 shrink-0" />
          <span className="flex-1 truncate text-left">
            {selectedRepo ? (selectedLabel ?? selectedRepo) : placeholder}
          </span>
          <CaretDownIcon className="size-3 shrink-0 opacity-70" />
        </PopoverTrigger>
        <PopoverContent
          align="start"
          side={side}
          className={cn(
            "flex max-h-72 w-72 flex-col overflow-hidden rounded-tick border border-line bg-panel p-0 text-label text-ink shadow-popup",
            dropdownClassName
          )}
        >
          <div className="flex items-center border-b border-line">
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={searchPlaceholder}
              className="min-w-0 flex-1 bg-transparent px-2 py-1.5 text-ink outline-none placeholder:text-ink-subtle"
            />
            <button
              type="button"
              title="Refresh repositories"
              aria-label="Refresh repositories"
              disabled={refresh.isPending}
              onClick={() => refresh.mutate()}
              className="mr-1 cursor-pointer rounded-tick p-1 text-ink-subtle transition-colors hover:bg-muted disabled:cursor-default"
            >
              <ArrowsClockwiseIcon
                className={cn("size-3.5", refresh.isPending && "animate-spin")}
              />
            </button>
          </div>
          {allowArchived && repos?.some((repo) => repo.archived) && (
            <label className="flex cursor-pointer items-center gap-2 border-b border-line px-2 py-1.5 text-ink-subtle">
              <input
                type="checkbox"
                checked={showArchived}
                onChange={(event) => setShowArchived(event.target.checked)}
              />
              Show archived
            </label>
          )}
          <div className="overflow-y-auto">
            <button
              type="button"
              onClick={() => {
                onRepoChange(null)
                setOpen(false)
                setQuery("")
              }}
              className={cn(
                "flex w-full items-center px-2 py-1.5 text-left transition-colors hover:bg-muted",
                selectedRepo ? "text-ink-subtle" : "text-ink"
              )}
            >
              {emptySelectionLabel}
              {!selectedRepo && (
                <span className="ml-auto pl-3 text-ink-subtle">✓</span>
              )}
            </button>
            {filteredRepos.length === 0 ? (
              <div className="px-2 py-1.5 text-ink-subtle">
                {noMatchesLabel}
              </div>
            ) : (
              filteredRepos.map((repo) => {
                const selected = repo.full_name === selectedRepo
                return (
                  <button
                    key={repo.full_name}
                    type="button"
                    title={repo.full_name}
                    onClick={() => {
                      onRepoChange(repo.full_name)
                      setOpen(false)
                      setQuery("")
                    }}
                    className={cn(
                      "flex w-full items-center px-2 py-1.5 text-left transition-colors hover:bg-muted",
                      selected ? "text-ink" : "text-ink-subtle"
                    )}
                  >
                    <span className="min-w-0 flex-1 truncate">
                      {repo.full_name}
                    </span>
                    {repo.private !== undefined && (
                      <span className="ml-2 shrink-0 rounded-tick border border-line px-1 text-meta text-ink-subtle">
                        {repo.private ? "Private" : "Public"}
                        {repo.archived ? " archive" : ""}
                      </span>
                    )}
                    {selected && (
                      <span className="ml-auto pl-3 text-ink-subtle">
                        ✓
                      </span>
                    )}
                  </button>
                )
              })
            )}
          </div>
        </PopoverContent>
      </div>
    </Popover>
  )
}
