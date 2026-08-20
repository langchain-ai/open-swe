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
  /** Omitted when the surface has only one diff source to read. */
  scope?: DiffScopeKind
  onScopeChange?: (scope: DiffScopeKind) => void
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
}: Pick<
  ChangesPanelProps,
  "status" | "isLoading" | "error" | "scope"
>): string {
  if (isLoading) return "Reading changes…"
  if (error) return errorMessage(error) ?? "Could not load changes."
  if (status === "missing")
    return "Changes are not available for this workspace."
  if (status === "error") return "Could not read changes. Try refreshing."
  if (scope === "pull-request") return "This pull request has no changes."
  return "No changes yet."
}

function ScopeSwitcher(props: {
  scope: DiffScopeKind
  prNumber: number | null
  branch?: string | null
  onScopeChange: (scope: DiffScopeKind) => void
}) {
  const [open, setOpen] = useState(false)
  const threadLabel = props.branch ? `Changes · ${props.branch}` : "Changes"
  const prLabel =
    props.prNumber === null ? "Pull request" : `Pull request #${props.prNumber}`
  const label = props.scope === "pull-request" ? prLabel : threadLabel

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
        <MenuItem onClick={() => props.onScopeChange("thread")}>
          {threadLabel}
        </MenuItem>
        {props.prNumber === null ? null : (
          <MenuItem onClick={() => props.onScopeChange("pull-request")}>
            {prLabel}
          </MenuItem>
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
          scope && onScopeChange ? (
            <ScopeSwitcher
              scope={scope}
              prNumber={pr?.number ?? null}
              branch={branch}
              onScopeChange={onScopeChange}
            />
          ) : (
            <span className="min-w-0 truncate text-sm font-medium text-foreground">
              Changes{branch ? ` · ${branch}` : ""}
            </span>
          )
        }
        actions={actions}
      />
    </div>
  )
}
