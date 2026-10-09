import { useEffect, useMemo, useRef, useState, type ReactNode } from "react"
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
import { useSession } from "@/lib/session"
import { useRecentRepos } from "@/lib/recentRepos"
import { OwnershipPicker, type OwnershipPickerProps } from "./OwnershipPicker"
import { cn } from "@/lib/utils"

type RepoOption = {
  full_name: string
  label?: string
  private?: boolean
  archived?: boolean
}

interface CommonProps {
  repos?: Array<RepoOption>
  selectedLabel?: string
  placeholder?: string
  emptySelectionLabel?: string
  allowEmpty?: boolean
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
  allowArchived?: boolean
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
    allowEmpty = true,
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
    allowArchived = false,
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
      repos?.some((repo) => repo.full_name === id && !repo.archived)
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
  const [showArchived, setShowArchived] = useState(false)
  const refresh = useRefreshRepos()

  const filteredRepos = useMemo(() => {
    const all = prioritize(repos ?? [], history, (repo) => repo.full_name)
    const q = query.trim().toLowerCase()
    return all.filter(
      (repo) =>
        ((allowArchived && showArchived) || !repo.archived) &&
        repo.full_name.toLowerCase().includes(q)
    )
  }, [repos, query, history, allowArchived, showArchived])

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
            aria-label={label}
            title={selected.length ? selected.join(", ") : undefined}
            className={cn(
              "flex max-w-[260px] cursor-pointer items-center gap-1 text-secondary transition-opacity hover:opacity-80 disabled:cursor-default disabled:opacity-60",
              triggerClassName
            )}
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
                refreshable && (
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
                )
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
          <div
            className="min-h-0 flex-1 overflow-y-auto"
            role={props.multiple ? "menu" : undefined}
          >
            {allowEmpty && (
              <button
                type="button"
                onClick={() => choose(null)}
                className={cn(
                  "flex w-full items-center px-2 py-1.5 text-left transition-colors hover:bg-elevated-hover",
                  selected.length ? "text-secondary" : "text-primary"
                )}
              >
                {props.multiple ? "Clear selection" : emptySelectionLabel}
                {selected.length === 0 && (
                  <span
                    aria-hidden="true"
                    className="ml-auto pl-3 text-secondary"
                  >
                    ✓
                  </span>
                )}
              </button>
            )}
            {filteredRepos.length === 0 ? (
              <div className="px-space-2 py-1.5 text-secondary">
                {noMatchesLabel}
              </div>
            ) : (
              filteredRepos.map((repo) => {
                const checked = selected.includes(repo.full_name)
                return (
                  <button
                    key={repo.full_name}
                    title={repo.full_name}
                    type="button"
                    aria-label={`${repo.label ?? repo.full_name}${repo.private === undefined ? "" : ` ${repo.private ? "Private" : "Public"}${repo.archived ? " archive" : ""}`}`}
                    onClick={() => choose(repo.full_name)}
                    role={props.multiple ? "menuitemcheckbox" : undefined}
                    aria-checked={props.multiple ? checked : undefined}
                    aria-pressed={props.multiple ? undefined : checked}
                    className={cn(
                      "flex w-full items-center gap-space-2 px-space-2 py-1.5 text-left transition-colors hover:bg-elevated-hover",
                      checked ? "text-primary" : "text-secondary"
                    )}
                  >
                    <span className="truncate">
                      {repo.label ?? repo.full_name}
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
                    {checked && (
                      <CheckIcon
                        size={12}
                        weight="regular"
                        aria-hidden="true"
                        className="shrink-0 text-icon-secondary"
                      />
                    )}
                  </button>
                )
              })
            )}
          </div>
          {footer}
        </PopoverContent>
      </div>
    </Popover>
  )
}
