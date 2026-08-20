import { useMemo, useState } from "react"
import {
  ChevronDownIcon,
  GitPullRequestIcon,
  RefreshCwIcon,
} from "lucide-react"

import type { AgentThread } from "@/features/agents/lib/types"
import type { DiffScopeKind } from "@/features/agents/lib/diffPanelStore"
import type { PanelFile } from "@/features/agents/components/DiffFilesView"
import { DiffFilesView } from "@/features/agents/components/DiffFilesView"
import { Menu, MenuItem, MenuPopup, MenuTrigger } from "@/components/ui/menu"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"

export type ChangesStatus = "ready" | "missing" | "error"

interface ChangesPanelProps {
  files: Array<PanelFile>
  status?: ChangesStatus
  isLoading: boolean
  isFetching: boolean
  error?: unknown
  truncated?: boolean
  branch?: string | null
  pr?: AgentThread["pr"] | null
  revealFilePath?: string | null
  fullScreen: boolean
  onRefresh: () => void
  extraActions?: React.ReactNode
  scope: DiffScopeKind
  onScopeChange: (scope: DiffScopeKind) => void
  /** False when nothing tells us what this branch is based on. */
  branchScopeAvailable: boolean
}

function errorMessage(error: unknown): string | null {
  if (!error) return null
  return error instanceof Error ? error.message : "Could not load changes."
}

export function changesEmptyLabel({
  status,
  isLoading,
  error,
  scope,
}: Pick<ChangesPanelProps, "status" | "isLoading" | "error"> & {
  scope?: DiffScopeKind
}): string {
  if (isLoading) return "Reading changes…"
  if (error) return errorMessage(error) ?? "Could not load changes."
  if (status === "missing")
    return "Changes are not available for this workspace."
  if (status === "error") return "Could not read changes. Try refreshing."
  if (scope === "branch") return "This branch changes nothing yet."
  return "No changes yet."
}

const SCOPE_LABELS: Record<DiffScopeKind, string> = {
  "working-tree": "Working tree",
  branch: "Branch changes",
}

function ScopeSwitcher(props: {
  scope: DiffScopeKind
  branchScopeAvailable: boolean
  onScopeChange: (scope: DiffScopeKind) => void
}) {
  const [open, setOpen] = useState(false)
  const label = SCOPE_LABELS[props.scope]

  const branchItem = (
    <MenuItem
      className={
        props.branchScopeAvailable
          ? undefined
          : "data-disabled:pointer-events-auto"
      }
      disabled={!props.branchScopeAvailable}
      onClick={() => props.onScopeChange("branch")}
    >
      {SCOPE_LABELS.branch}
    </MenuItem>
  )

  return (
    <Menu open={open} onOpenChange={setOpen}>
      <MenuTrigger
        className="flex h-6 min-w-0 cursor-pointer items-center gap-1 rounded-md px-1.5 text-sm font-medium text-foreground transition-colors hover:bg-accent"
        aria-label={`Diff scope: ${label}`}
      >
        <span className="min-w-0 truncate">{label}</span>
        <ChevronDownIcon className="size-3.5 shrink-0 opacity-70" />
      </MenuTrigger>
      <MenuPopup
        align="start"
        side="bottom"
        sideOffset={6}
        className="min-w-52"
      >
        <MenuItem onClick={() => props.onScopeChange("working-tree")}>
          {SCOPE_LABELS["working-tree"]}
        </MenuItem>
        {props.branchScopeAvailable ? (
          branchItem
        ) : (
          <Tooltip>
            <TooltipTrigger render={branchItem} />
            <TooltipPopup side="right">
              This thread has no branch to compare against its base yet.
            </TooltipPopup>
          </Tooltip>
        )}
      </MenuPopup>
    </Menu>
  )
}

export function ChangesPanel({
  files,
  status,
  isLoading,
  isFetching,
  error,
  truncated,
  branch,
  pr,
  revealFilePath,
  fullScreen,
  onRefresh,
  extraActions,
  scope,
  onScopeChange,
  branchScopeAvailable,
}: ChangesPanelProps) {
  const emptyLabel = changesEmptyLabel({ status, isLoading, error, scope })
  const actions = useMemo(
    () => (
      <>
        <button
          type="button"
          aria-label="Refresh changes"
          title="Refresh changes"
          onClick={onRefresh}
          disabled={isFetching}
          className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
        >
          <RefreshCwIcon
            className={isFetching ? "size-3.5 animate-spin" : "size-3.5"}
          />
        </button>
        {extraActions}
        {pr && (
          <a
            href={pr.url}
            target="_blank"
            rel="noreferrer"
            className="flex h-7 items-center gap-1.5 rounded-md border border-border px-2 text-xs font-medium text-foreground transition-colors hover:bg-accent"
          >
            <GitPullRequestIcon className="size-3.5" />
            View PR
          </a>
        )}
      </>
    ),
    [extraActions, isFetching, onRefresh, pr]
  )

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {truncated && (
        <div className="shrink-0 border-b border-border bg-warning/10 px-3 py-2 text-xs text-warning-foreground">
          Only the first {files.length} changed file
          {files.length === 1 ? " is" : "s are"} shown.
        </div>
      )}
      <DiffFilesView
        files={files}
        revealFilePath={revealFilePath}
        fullScreen={fullScreen}
        emptyLabel={emptyLabel}
        truncated={truncated}
        leading={
          <div className="flex min-w-0 items-center gap-1.5">
            <ScopeSwitcher
              scope={scope}
              branchScopeAvailable={branchScopeAvailable}
              onScopeChange={onScopeChange}
            />
            {branch && (
              <span className="min-w-0 truncate text-xs text-muted-foreground">
                {branch}
              </span>
            )}
          </div>
        }
        actions={actions}
      />
    </div>
  )
}
