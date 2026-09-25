import { useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import {
  ArrowsClockwiseIcon,
  CaretDownIcon,
  FolderIcon,
} from "@phosphor-icons/react"

import { Popover, PopoverPopup, PopoverTrigger } from "@/components/ui/popover"
import { useRefreshRepos } from "@/lib/profile"
import { useSession } from "@/lib/session"
import { useRecentRepos } from "@/lib/recentRepos"
import { OwnershipPicker, type OwnershipPickerProps } from "./OwnershipPicker"
import { cn } from "@/lib/utils"

type RepoOption = { full_name: string; label?: string }

interface CommonProps {
  repos?: Array<RepoOption>
  selectedLabel?: string
  placeholder?: string
  emptySelectionLabel?: string
  searchPlaceholder?: string
  noMatchesLabel?: string
  className?: string
  triggerClassName?: string
  dropdownClassName?: string
  side?: "top" | "bottom"
  disabled?: boolean
  label?: string
  autoSelect?: boolean
  historyScope?: string
  footer?: ReactNode
  refreshable?: boolean
}

type RepoSelectorProps = CommonProps &
  (
    | {
        multiple?: false
        selectedRepo?: string | null
        onRepoChange: (repo: string | null) => void
      }
    | {
        multiple: true
        selectedRepos: string[]
        onReposChange: (repos: string[]) => void
      }
  )

type OwnershipProps = { ownership: OwnershipPickerProps }

export function RepoSelector(props: RepoSelectorProps | OwnershipProps) {
  const session = useSession()
  const scope =
    "ownership" in props ? "github" : (props.historyScope ?? "github")
  const key = `${session.data?.login ?? "anonymous"}:${scope}`
  const history = useRecentRepos((state) => state.byAccount[key] ?? EMPTY)
  const remember = (repo: string) => {
    if (session.data?.login) useRecentRepos.getState().remember(key, repo)
  }
  if ("ownership" in props) {
    const { ownership } = props
    return (
      <OwnershipPicker
        {...ownership}
        items={prioritize(ownership.items, history, (item) => item.id)}
        onChange={(selected) => {
          selected
            .filter((id) => !ownership.selected.includes(id))
            .forEach(remember)
          ownership.onChange(selected)
        }}
      />
    )
  }
  return (
    <RepoSelect key={key} {...props} history={history} remember={remember} />
  )
}

const EMPTY: string[] = []

function prioritize<T>(
  items: T[],
  history: string[],
  id: (item: T) => string
): T[] {
  const rank = (item: T) => {
    const index = history.indexOf(id(item))
    return index < 0 ? history.length : index
  }
  return [...items].sort((a, b) => rank(a) - rank(b))
}

function RepoSelect(
  props: RepoSelectorProps & {
    history: string[]
    remember: (repo: string) => void
  }
) {
  const {
    repos,
    selectedLabel,
    placeholder = "Select repository",
    emptySelectionLabel = "No repository",
    searchPlaceholder = "Search repositories…",
    noMatchesLabel = "No matches",
    className,
    triggerClassName,
    dropdownClassName,
    side = "bottom",
    disabled = false,
    label,
    autoSelect = true,
    footer,
    refreshable = true,
    history,
    remember,
  } = props
  const selected = props.multiple
    ? props.selectedRepos
    : props.selectedRepo
      ? [props.selectedRepo]
      : []
  const initialized = useRef(false)
  useEffect(() => {
    if (initialized.current || disabled || props.multiple || !autoSelect) return
    if (props.selectedRepo) {
      initialized.current = true
      return
    }
    const recent = history.find((id) =>
      repos?.some((repo) => repo.full_name === id)
    )
    if (recent) {
      initialized.current = true
      props.onRepoChange(recent)
    }
  }, [props, repos, history, disabled, autoSelect])
  const choose = (repo: string | null) => {
    initialized.current = true
    if (repo && (!props.multiple || !selected.includes(repo))) remember(repo)
    if (props.multiple) {
      props.onReposChange(
        repo === null
          ? []
          : selected.includes(repo)
            ? selected.filter((id) => id !== repo)
            : [...selected, repo]
      )
    } else {
      props.onRepoChange(repo)
      setOpen(false)
      setQuery("")
    }
  }
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const refresh = useRefreshRepos()

  const filteredRepos = useMemo(() => {
    const all = prioritize(repos ?? [], history, (repo) => repo.full_name)
    const q = query.trim().toLowerCase()
    if (!q) return all
    return all.filter((repo) => repo.full_name.toLowerCase().includes(q))
  }, [repos, query, history])

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
              aria-label={label}
              className={cn(
                "flex max-w-[260px] cursor-pointer items-center gap-1 text-muted-foreground transition-opacity hover:opacity-80 disabled:cursor-default disabled:opacity-60",
                triggerClassName
              )}
            />
          }
        >
          <FolderIcon className="size-3.5 shrink-0" />
          <span className="flex-1 truncate text-left">
            {selected.length
              ? (selectedLabel ??
                (props.multiple
                  ? `${selected.length} repositories`
                  : (repos?.find((repo) => repo.full_name === selected[0])
                      ?.label ?? selected[0])))
              : placeholder}
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
              aria-label={searchPlaceholder}
              className="min-w-0 flex-1 bg-transparent px-2 py-1.5 text-foreground outline-none placeholder:text-muted-foreground"
            />
            {refreshable && (
              <button
                type="button"
                title="Refresh repositories"
                aria-label="Refresh repositories"
                disabled={refresh.isPending}
                onClick={() => refresh.mutate()}
                className="mr-1 cursor-pointer rounded p-1 text-muted-foreground transition-colors hover:bg-muted disabled:cursor-default"
              >
                <ArrowsClockwiseIcon
                  className={cn(
                    "size-3.5",
                    refresh.isPending && "animate-spin"
                  )}
                />
              </button>
            )}
          </div>
          <div
            className="overflow-y-auto"
            role={props.multiple ? "menu" : undefined}
          >
            <button
              type="button"
              onClick={() => choose(null)}
              className={cn(
                "flex w-full items-center px-2 py-1.5 text-left transition-colors hover:bg-muted",
                selected.length ? "text-muted-foreground" : "text-foreground"
              )}
            >
              {props.multiple ? "Clear selection" : emptySelectionLabel}
              {selected.length === 0 && (
                <span
                  aria-hidden="true"
                  className="ml-auto pl-3 text-muted-foreground"
                >
                  ✓
                </span>
              )}
            </button>
            {filteredRepos.length === 0 ? (
              <div className="px-2 py-1.5 text-muted-foreground">
                {noMatchesLabel}
              </div>
            ) : (
              filteredRepos.map((repo) => {
                const checked = selected.includes(repo.full_name)
                return (
                  <button
                    key={repo.full_name}
                    type="button"
                    aria-label={repo.label ?? repo.full_name}
                    onClick={() => choose(repo.full_name)}
                    role={props.multiple ? "menuitemcheckbox" : undefined}
                    aria-checked={props.multiple ? checked : undefined}
                    aria-pressed={props.multiple ? undefined : checked}
                    className={cn(
                      "flex w-full items-center px-2 py-1.5 text-left transition-colors hover:bg-muted",
                      checked ? "text-foreground" : "text-muted-foreground"
                    )}
                  >
                    <span className="truncate">
                      {repo.label ?? repo.full_name}
                    </span>
                    {history.includes(repo.full_name) && (
                      <span className="ml-auto pl-3 text-[10px] text-muted-foreground">
                        Recent
                      </span>
                    )}
                    {checked && (
                      <span
                        aria-hidden="true"
                        className="ml-auto pl-3 text-muted-foreground"
                      >
                        ✓
                      </span>
                    )}
                  </button>
                )
              })
            )}
          </div>
          {footer}
        </PopoverPopup>
      </div>
    </Popover>
  )
}
