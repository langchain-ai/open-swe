import { useId, useMemo, useState } from "react"

import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import {
  Button,
  buttonVariants,
} from "@langchain/gtm-platform-design-system/ui/button"
import { Checkbox } from "@langchain/gtm-platform-design-system/ui/checkbox"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/gtm-platform-design-system/ui/popover"
import { ScrollAreaBody } from "@langchain/gtm-platform-design-system/ui/scroll-area"
import { Check, ChevronDown, Folder, RefreshCw } from "@/components/glyphs"
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
  /**
   * `inline` is the quiet text trigger for toolbars and composers; `field`
   * draws an outline control for a settings row or form.
   */
  appearance?: "inline" | "field"
}

const INLINE_TRIGGER_CLASS =
  "flex max-w-65 cursor-pointer items-center gap-1 text-ink-subtle transition-opacity duration-fast ease-out-quint hover:opacity-80 disabled:cursor-default disabled:opacity-60 motion-reduce:transition-none"

const OPTION_CLASS =
  "flex w-full cursor-pointer items-center gap-2 rounded-compact px-2 py-1.5 text-left text-label transition-colors duration-fast ease-out-quint hover:bg-hover motion-reduce:transition-none"

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
  appearance = "inline",
}: RepoSelectorProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [showArchived, setShowArchived] = useState(false)
  const refresh = useRefreshRepos()
  const archivedLabelId = useId()
  const field = appearance === "field"

  const filteredRepos = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (repos ?? []).filter(
      (repo) =>
        ((allowArchived && showArchived) || !repo.archived) &&
        repo.full_name.toLowerCase().includes(q)
    )
  }, [repos, query, showArchived, allowArchived])

  const choose = (repo: string | null) => {
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
            <button
              type="button"
              disabled={disabled}
              title={selectedRepo ?? undefined}
              className={cn(
                field
                  ? buttonVariants({
                      variant: "outline",
                      className: "w-full justify-between font-normal",
                    })
                  : INLINE_TRIGGER_CLASS,
                triggerClassName
              )}
            />
          }
        >
          <Icon icon={Folder} size="sm" className="text-ink-subtle" />
          <span className="min-w-0 flex-1 truncate text-left">
            {selectedRepo ? (selectedLabel ?? selectedRepo) : placeholder}
          </span>
          <Icon icon={ChevronDown} size="sm" className="text-ink-subtle" />
        </PopoverTrigger>
        <PopoverContent
          inset="flush"
          align="start"
          side={side}
          className={cn("flex max-h-72 w-72 flex-col", dropdownClassName)}
        >
          <Inline
            gap="xs"
            align="center"
            className="border-b border-line py-1 pr-1 pl-2.5"
          >
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={searchPlaceholder}
              aria-label={searchPlaceholder}
              className="h-control-sm min-w-0 flex-1 bg-transparent text-label text-ink outline-none placeholder:text-ink-subtle"
            />
            <Button
              size="icon-sm"
              variant="ghost"
              title="Refresh repositories"
              aria-label="Refresh repositories"
              disabled={refresh.isPending}
              onClick={() => refresh.mutate()}
            >
              <Icon
                icon={RefreshCw}
                size="sm"
                className={cn(
                  "text-ink-subtle",
                  refresh.isPending && "animate-spin"
                )}
              />
            </Button>
          </Inline>
          {allowArchived && repos?.some((repo) => repo.archived) && (
            <Inline
              render={<label />}
              gap="sm"
              align="center"
              className="cursor-pointer border-b border-line px-2.5 py-1.5 text-label text-ink-subtle"
            >
              <Checkbox
                aria-labelledby={archivedLabelId}
                checked={showArchived}
                onCheckedChange={setShowArchived}
              />
              <span id={archivedLabelId}>Show archived</span>
            </Inline>
          )}
          <ScrollAreaBody overflow="vertical">
            <Box padding="xs">
              <button
                type="button"
                onClick={() => choose(null)}
                className={cn(
                  OPTION_CLASS,
                  selectedRepo ? "text-ink-subtle" : "text-ink"
                )}
              >
                <span className="min-w-0 flex-1 truncate">
                  {emptySelectionLabel}
                </span>
                {!selectedRepo && (
                  <Icon icon={Check} size="sm" className="text-ink-subtle" />
                )}
              </button>
              {filteredRepos.length === 0 ? (
                <Box className="px-2 py-1.5 text-label text-ink-subtle">
                  {noMatchesLabel}
                </Box>
              ) : (
                filteredRepos.map((repo) => {
                  const selected = repo.full_name === selectedRepo
                  return (
                    <button
                      key={repo.full_name}
                      type="button"
                      title={repo.full_name}
                      onClick={() => choose(repo.full_name)}
                      className={cn(
                        OPTION_CLASS,
                        selected ? "text-ink" : "text-ink-subtle"
                      )}
                    >
                      <span className="min-w-0 flex-1 truncate">
                        {repo.full_name}
                      </span>
                      {repo.private !== undefined && (
                        <Badge tier="quiet" tone="neutral">
                          {repo.private ? "Private" : "Public"}
                          {repo.archived ? " archive" : ""}
                        </Badge>
                      )}
                      {selected && (
                        <Icon
                          icon={Check}
                          size="sm"
                          className="text-ink-subtle"
                        />
                      )}
                    </button>
                  )
                })
              )}
            </Box>
          </ScrollAreaBody>
        </PopoverContent>
      </div>
    </Popover>
  )
}
