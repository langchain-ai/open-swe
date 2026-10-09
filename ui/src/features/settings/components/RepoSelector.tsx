import { useMemo, useState } from "react"
import { Badge } from "@langchain/macaw-components/Badge"
import { Checkbox } from "@langchain/macaw-components/Checkbox"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Input } from "@langchain/macaw-components/Input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"
import { ArrowsClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowsClockwise"
import { CaretDownIcon } from "@phosphor-icons/react/dist/ssr/CaretDown"
import { CheckIcon } from "@phosphor-icons/react/dist/ssr/Check"
import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"

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
        <PopoverTrigger asChild>
          <button
            type="button"
            disabled={disabled}
            title={selectedRepo ?? undefined}
            className={cn(
              "flex max-w-[260px] cursor-pointer items-center gap-space-1 text-secondary transition-opacity hover:opacity-80 disabled:cursor-default disabled:opacity-60",
              triggerClassName
            )}
          >
            <FolderIcon size={14} weight="regular" className="shrink-0" />
            <span className="flex-1 truncate text-left">
              {selectedRepo ? (selectedLabel ?? selectedRepo) : placeholder}
            </span>
            <CaretDownIcon
              size={12}
              weight="regular"
              className="shrink-0 opacity-70"
            />
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          side={side}
          className={cn(
            "flex max-h-72 w-72 flex-col overflow-hidden bg-elevated p-0 text-xs text-primary",
            dropdownClassName
          )}
        >
          <div className="border-b border-default">
            <Input
              autoFocus
              size="sm"
              variant="plain"
              value={query}
              onChange={setQuery}
              placeholder={searchPlaceholder}
              aria-label={searchPlaceholder}
              rightDecorator={
                <IconButton
                  icon={ArrowsClockwiseIcon}
                  label="Refresh repositories"
                  size="xs"
                  color="secondary"
                  variant="plain"
                  loading={refresh.isPending}
                  disabled={refresh.isPending}
                  onClick={() => refresh.mutate()}
                />
              }
            />
          </div>
          {allowArchived && repos?.some((repo) => repo.archived) && (
            <Checkbox
              size="sm"
              label="Show archived"
              checked={showArchived}
              onCheckedChange={(checked) => setShowArchived(checked === true)}
              containerClassName="border-b border-default px-space-2 py-1.5"
              labelClassName="text-secondary"
            />
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
                "flex w-full items-center px-space-2 py-1.5 text-left transition-colors hover:bg-elevated-hover",
                selectedRepo ? "text-secondary" : "text-primary"
              )}
            >
              {emptySelectionLabel}
              {!selectedRepo && (
                <CheckIcon
                  size={12}
                  weight="regular"
                  aria-label="Selected"
                  className="ml-auto shrink-0 text-icon-secondary"
                />
              )}
            </button>
            {filteredRepos.length === 0 ? (
              <div className="px-space-2 py-1.5 text-secondary">
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
                      "flex w-full items-center gap-space-2 px-space-2 py-1.5 text-left transition-colors hover:bg-elevated-hover",
                      selected ? "text-primary" : "text-secondary"
                    )}
                  >
                    <span className="min-w-0 flex-1 truncate">
                      {repo.full_name}
                    </span>
                    {repo.private !== undefined && (
                      <Badge
                        color="secondary"
                        size="xxs"
                        rounded="xs"
                        className="shrink-0"
                      >
                        {`${repo.private ? "Private" : "Public"}${repo.archived ? " archive" : ""}`}
                      </Badge>
                    )}
                    {selected && (
                      <CheckIcon
                        size={12}
                        weight="regular"
                        aria-label="Selected"
                        className="shrink-0 text-icon-secondary"
                      />
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
