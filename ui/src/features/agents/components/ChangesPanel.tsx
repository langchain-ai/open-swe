import { CaretDownIcon } from "@langchain/macaw-components/icons"
import { useMemo, useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Tooltip } from "@langchain/macaw-components/Tooltip"
import { ArrowClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowClockwise"
import { GitBranchIcon } from "@phosphor-icons/react/dist/ssr/GitBranch"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"

import type { AgentThread } from "@/features/agents/lib/types"
import type { DiffScopeKind } from "@/features/agents/lib/diffPanelStore"
import type { PanelFile } from "@/features/agents/components/DiffFilesView"
import { DiffFilesView } from "@/features/agents/components/DiffFilesView"

export type ChangesStatus = "ready" | "missing" | "error"

interface ChangesPanelProps {
  onComment?: (content: string) => Promise<void>
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
    <DropdownMenuItem
      className={
        props.branchScopeAvailable
          ? undefined
          : "data-[disabled]:pointer-events-auto"
      }
      disabled={!props.branchScopeAvailable}
      onSelect={() => props.onScopeChange("branch")}
    >
      {SCOPE_LABELS.branch}
    </DropdownMenuItem>
  )

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger
        className="flex h-6 min-w-0 shrink cursor-pointer items-center gap-space-1 rounded-md px-space-1 text-sm font-medium text-primary transition-colors hover:bg-surface-level-1-hover"
        aria-label={`Diff scope: ${label}`}
      >
        <span className="min-w-0 truncate">{label}</span>
        <CaretDownIcon
          size={14}
          weight="regular"
          className="shrink-0 text-icon-secondary"
        />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        side="bottom"
        sideOffset={6}
        className="min-w-52"
      >
        <DropdownMenuItem onSelect={() => props.onScopeChange("working-tree")}>
          {SCOPE_LABELS["working-tree"]}
        </DropdownMenuItem>
        {props.branchScopeAvailable ? (
          branchItem
        ) : (
          <Tooltip
            title="This thread has no branch to compare against its base yet."
            side="right"
          >
            {branchItem}
          </Tooltip>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function ChangesPanel({
  files,
  onComment,
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
        <IconButton
          icon={ArrowClockwiseIcon}
          label="Refresh changes"
          size="sm"
          color="secondary"
          variant="plain"
          onClick={onRefresh}
          disabled={isFetching}
          loading={isFetching}
        />
        {extraActions}
        {pr && (
          <Button
            as={<a href={pr.url} target="_blank" rel="noreferrer" />}
            aria-label="View PR"
            title="View PR"
            color="secondary"
            variant="outlined"
            leftDecorator={GitPullRequestIcon}
            className="@max-[680px]:w-6 @max-[680px]:px-0"
          >
            <span className="whitespace-nowrap @max-[680px]:hidden">
              View PR
            </span>
          </Button>
        )}
      </>
    ),
    [extraActions, isFetching, onRefresh, pr]
  )

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {truncated && (
        <div className="shrink-0 border-b border-default bg-warning px-space-3 py-space-2 text-xs text-warning-secondary">
          Only the first {files.length} changed file
          {files.length === 1 ? " is" : "s are"} shown.
        </div>
      )}
      <DiffFilesView
        files={files}
        onComment={onComment}
        revealFilePath={revealFilePath}
        fullScreen={fullScreen}
        emptyLabel={emptyLabel}
        truncated={truncated}
        leading={
          <div className="flex min-w-0 items-center gap-space-2">
            <ScopeSwitcher
              scope={scope}
              branchScopeAvailable={branchScopeAvailable}
              onScopeChange={onScopeChange}
            />
            {branch && (
              <>
                <span
                  className="min-w-0 truncate text-xs text-secondary @max-[520px]:hidden"
                  title={branch}
                >
                  {branch}
                </span>
                <IconButton
                  icon={GitBranchIcon}
                  label={`Branch: ${branch}`}
                  tooltipProps={{ title: branch }}
                  size="sm"
                  color="secondary"
                  variant="plain"
                  className="hidden @max-[520px]:inline-flex"
                />
              </>
            )}
          </div>
        }
        actions={actions}
      />
    </div>
  )
}
