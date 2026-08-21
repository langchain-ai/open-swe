import { useMemo } from "react"
import { GitPullRequestIcon, RefreshCwIcon } from "lucide-react"

import type { AgentThread } from "@/lib/agentTypes"
import type { PanelFile } from "@/components/diff/DiffFilesView"
import { DiffFilesView } from "@/components/diff/DiffFilesView"

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
}

function errorMessage(error: unknown): string | null {
  if (!error) return null
  return error instanceof Error ? error.message : "Could not load changes."
}

export function changesEmptyLabel({
  status,
  isLoading,
  error,
}: Pick<ChangesPanelProps, "status" | "isLoading" | "error">): string {
  if (isLoading) return "Reading changes…"
  if (error) return errorMessage(error) ?? "Could not load changes."
  if (status === "missing")
    return "Changes are not available for this workspace."
  if (status === "error") return "Could not read changes. Try refreshing."
  return "No changes yet."
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
}: ChangesPanelProps) {
  const emptyLabel = changesEmptyLabel({ status, isLoading, error })
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
          <span className="min-w-0 truncate text-sm font-medium text-foreground">
            Changes{branch ? ` · ${branch}` : ""}
          </span>
        }
        actions={actions}
      />
    </div>
  )
}
