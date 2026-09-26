import { useMemo, useState } from "react"
import {
  ArrowsClockwiseIcon,
  CaretDownIcon,
  FolderIcon,
} from "@phosphor-icons/react"

import { Button } from "@/components/ui/button"
import {
  Command,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { Popover, PopoverPopup, PopoverTrigger } from "@/components/ui/popover"
import { Spinner } from "@/components/ui/spinner"
import { TooltipIconButton } from "@/components/ui/tooltip-icon-button"
import { useRefreshRepos } from "@/lib/profile"
import { cn } from "@/lib/utils"

type RepoOption = { full_name: string }

const EMPTY_SELECTION_VALUE = "__no_repository__"

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

  const filteredRepos = useMemo(() => {
    const all = repos ?? []
    const q = query.trim().toLowerCase()
    if (!q) return all
    return all.filter((repo) => repo.full_name.toLowerCase().includes(q))
  }, [repos, query])

  const select = (repo: string | null) => {
    onRepoChange(repo)
    setOpen(false)
    setQuery("")
  }

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
            <Button
              variant="ghost"
              disabled={disabled}
              className={cn(
                "h-auto max-w-[260px] justify-start gap-1 border-0 p-0 font-normal text-muted-foreground transition-opacity hover:bg-transparent hover:opacity-80 disabled:opacity-60 aria-expanded:bg-transparent",
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
          <Command shouldFilter={false} className="rounded-none">
            <div className="flex items-center gap-1 pr-1">
              <div className="min-w-0 flex-1">
                <CommandInput
                  autoFocus
                  value={query}
                  onValueChange={setQuery}
                  placeholder={searchPlaceholder}
                />
              </div>
              <TooltipIconButton
                label="Refresh repositories"
                disabled={refresh.isPending}
                onClick={() => refresh.mutate()}
                className="mt-1"
              >
                {refresh.isPending ? <Spinner /> : <ArrowsClockwiseIcon />}
              </TooltipIconButton>
            </div>
            <CommandList>
              <CommandGroup>
                <CommandItem
                  value={EMPTY_SELECTION_VALUE}
                  data-checked={!selectedRepo}
                  onSelect={() => select(null)}
                  className={
                    selectedRepo ? "text-muted-foreground" : "text-foreground"
                  }
                >
                  {emptySelectionLabel}
                </CommandItem>
                {filteredRepos.length === 0 ? (
                  <div className="px-2.5 py-1.5 text-muted-foreground">
                    {noMatchesLabel}
                  </div>
                ) : (
                  filteredRepos.map((repo) => {
                    const selected = repo.full_name === selectedRepo
                    return (
                      <CommandItem
                        key={repo.full_name}
                        value={repo.full_name}
                        data-checked={selected}
                        onSelect={() => select(repo.full_name)}
                        className={
                          selected ? "text-foreground" : "text-muted-foreground"
                        }
                      >
                        <span className="truncate">{repo.full_name}</span>
                      </CommandItem>
                    )
                  })
                )}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverPopup>
      </div>
    </Popover>
  )
}
