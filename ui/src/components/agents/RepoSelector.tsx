import { useEffect, useMemo, useRef, useState } from "react"
import { CaretDownIcon, FolderIcon } from "@phosphor-icons/react"

import { cn } from "@/lib/utils"

type RepoOption = { full_name: string }

interface RepoSelectorProps {
  repos?: Array<RepoOption>
  selectedRepo?: string | null
  onRepoChange: (repo: string | null) => void
  placeholder?: string
  emptySelectionLabel?: string
  searchPlaceholder?: string
  noMatchesLabel?: string
  className?: string
  triggerClassName?: string
  dropdownClassName?: string
  disabled?: boolean
}

export function RepoSelector({
  repos,
  selectedRepo = null,
  onRepoChange,
  placeholder = "Select repository",
  emptySelectionLabel = "No repository",
  searchPlaceholder = "Search repositories…",
  noMatchesLabel = "No matches",
  className,
  triggerClassName,
  dropdownClassName,
  disabled = false,
}: RepoSelectorProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const dropdownRef = useRef<HTMLDivElement>(null)

  const filteredRepos = useMemo(() => {
    const all = repos ?? []
    const q = query.trim().toLowerCase()
    if (!q) return all
    return all.filter((repo) => repo.full_name.toLowerCase().includes(q))
  }, [repos, query])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      const target = e.target as Node
      if (dropdownRef.current && !dropdownRef.current.contains(target)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  return (
    <div ref={dropdownRef} className={cn("relative min-w-0 shrink", className)}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "flex max-w-[260px] cursor-pointer items-center gap-1 text-muted-foreground transition-opacity hover:opacity-80 disabled:cursor-default disabled:opacity-60",
          triggerClassName
        )}
      >
        <FolderIcon className="size-3.5 shrink-0" />
        <span className="flex-1 truncate text-left">
          {selectedRepo || placeholder}
        </span>
        <CaretDownIcon className="size-3 shrink-0 opacity-70" />
      </button>
      {open && (
        <div
          className={cn(
            "absolute top-full left-0 z-50 mt-1 flex max-h-72 w-72 flex-col overflow-hidden rounded border border-border bg-popover text-xs text-popover-foreground shadow-lg",
            dropdownClassName
          )}
        >
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={searchPlaceholder}
            className="w-full border-b border-border bg-transparent px-2 py-1.5 text-foreground outline-none placeholder:text-muted-foreground"
          />
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
        </div>
      )}
    </div>
  )
}
