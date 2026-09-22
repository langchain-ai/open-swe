import { useMemo, useState } from "react"
import {
  ArrowsClockwiseIcon,
  CaretDownIcon,
  FolderIcon,
} from "@phosphor-icons/react"

import { Popover, PopoverPopup, PopoverTrigger } from "@/components/ui/popover"
import { prioritizeRepositories } from "@/lib/repositoryUsage"
import { useProfile, useRefreshRepos } from "@/lib/profile"
import { cn } from "@/lib/utils"

type RepoOption = { full_name: string }

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
}: RepoSelectorProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const refresh = useRefreshRepos()
  const profile = useProfile()

  const filteredRepos = useMemo(() => {
    const all = prioritizeRepositories(
      repos ?? [],
      profile.data?.repository_usage,
      (repo) => repo.full_name
    )
    const q = query.trim().toLowerCase()
    if (!q) return all
    return all.filter((repo) => repo.full_name.toLowerCase().includes(q))
  }, [repos, query, profile.data?.repository_usage])

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
              className={cn(
                "flex max-w-[260px] cursor-pointer items-center gap-1 text-muted-foreground transition-opacity hover:opacity-80 disabled:cursor-default disabled:opacity-60",
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
        <PopoverPopup
          align="start"
          side={side}
          className={cn(
            "flex max-h-72 w-72 flex-col overflow-hidden rounded border border-border bg-popover p-0 text-xs text-popover-foreground shadow-lg",
            dropdownClassName
          )}
        >
          <div className="flex items-center border-b border-border">
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={searchPlaceholder}
              className="min-w-0 flex-1 bg-transparent px-2 py-1.5 text-foreground outline-none placeholder:text-muted-foreground"
            />
            <button
              type="button"
              title="Refresh repositories"
              aria-label="Refresh repositories"
              disabled={refresh.isPending}
              onClick={() => refresh.mutate()}
              className="mr-1 cursor-pointer rounded p-1 text-muted-foreground transition-colors hover:bg-muted disabled:cursor-default"
            >
              <ArrowsClockwiseIcon
                className={cn("size-3.5", refresh.isPending && "animate-spin")}
              />
            </button>
          </div>
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
                selectedRepo ? "text-muted-foreground" : "text-foreground"
              )}
            >
              {emptySelectionLabel}
              {!selectedRepo && (
                <span className="ml-auto pl-3 text-muted-foreground">✓</span>
              )}
            </button>
            {filteredRepos.length === 0 ? (
              <div className="px-2 py-1.5 text-muted-foreground">
                {noMatchesLabel}
              </div>
            ) : (
              filteredRepos.map((repo) => {
                const selected = repo.full_name === selectedRepo
                return (
                  <button
                    key={repo.full_name}
                    type="button"
                    onClick={() => {
                      onRepoChange(repo.full_name)
                      setOpen(false)
                      setQuery("")
                    }}
                    className={cn(
                      "flex w-full items-center px-2 py-1.5 text-left transition-colors hover:bg-muted",
                      selected ? "text-foreground" : "text-muted-foreground"
                    )}
                  >
                    <span className="truncate">{repo.full_name}</span>
                    {selected && (
                      <span className="ml-auto pl-3 text-muted-foreground">
                        ✓
                      </span>
                    )}
                  </button>
                )
              })
            )}
          </div>
        </PopoverPopup>
      </div>
    </Popover>
  )
}
