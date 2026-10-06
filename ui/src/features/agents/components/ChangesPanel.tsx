import { useMemo, useState } from "react"

import type { AgentThread } from "@/features/agents/lib/types"
import type { DiffScopeKind } from "@/features/agents/lib/diffPanelStore"
import type { PanelFile } from "@/features/agents/components/DiffFilesView"
import { DiffFilesView } from "@/features/agents/components/DiffFilesView"
import { PanelIconButton } from "@/features/agents/components/panel/PanelIconButton"
import {
  Alert,
  AlertDescription,
} from "@langchain/gtm-platform-design-system/ui/alert"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@langchain/gtm-platform-design-system/ui/dropdown-menu"
import { FadeText } from "@langchain/gtm-platform-design-system/ui/fade-text"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@langchain/gtm-platform-design-system/ui/tooltip"
import {
  ChevronDown,
  GitBranch,
  GitPullRequest,
  RefreshCw,
} from "@/components/glyphs"

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
          : "data-disabled:pointer-events-auto"
      }
      disabled={!props.branchScopeAvailable}
      onClick={() => props.onScopeChange("branch")}
    >
      {SCOPE_LABELS.branch}
    </DropdownMenuItem>
  )

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger
        render={
          <Button
            aria-label={`Diff scope: ${label}`}
            className="min-w-0 shrink gap-1 px-1.5"
            size="compact"
            variant="ghost"
          />
        }
      >
        <span className="min-w-0 truncate">{label}</span>
        <Icon icon={ChevronDown} size="sm" className="text-ink-subtle" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-52">
        <DropdownMenuItem onClick={() => props.onScopeChange("working-tree")}>
          {SCOPE_LABELS["working-tree"]}
        </DropdownMenuItem>
        {props.branchScopeAvailable ? (
          branchItem
        ) : (
          <Tooltip>
            <TooltipTrigger render={branchItem} />
            <TooltipContent side="right">
              This thread has no branch to compare against its base yet.
            </TooltipContent>
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
        <PanelIconButton
          label="Refresh changes"
          onClick={onRefresh}
          disabled={isFetching}
        >
          <Icon
            icon={RefreshCw}
            size="sm"
            className={
              isFetching ? "animate-spin motion-reduce:animate-none" : undefined
            }
          />
        </PanelIconButton>
        {extraActions}
        {pr && (
          <Button
            nativeButton={false}
            render={
              <a
                href={pr.url}
                target="_blank"
                rel="noreferrer"
                aria-label="View PR"
                title="View PR"
              />
            }
            variant="outline"
            size="compact"
            className="@max-2xl:w-control-sm @max-2xl:px-0"
          >
            <Icon icon={GitPullRequest} size="sm" />
            <span className="@max-2xl:hidden">View PR</span>
          </Button>
        )}
      </>
    ),
    [extraActions, isFetching, onRefresh, pr]
  )

  return (
    <Stack className="min-h-0 flex-1">
      {truncated && (
        <Box className="shrink-0 px-2 pt-2">
          <Alert tone="attention">
            <AlertDescription>
              Only the first {files.length} changed file
              {files.length === 1 ? " is" : "s are"} shown.
            </AlertDescription>
          </Alert>
        </Box>
      )}
      <DiffFilesView
        files={files}
        onComment={onComment}
        revealFilePath={revealFilePath}
        fullScreen={fullScreen}
        emptyLabel={emptyLabel}
        truncated={truncated}
        leading={
          <Inline gap="xs" className="min-w-0">
            <ScopeSwitcher
              scope={scope}
              branchScopeAvailable={branchScopeAvailable}
              onScopeChange={onScopeChange}
            />
            {branch && (
              <>
                <FadeText
                  lines={1}
                  render={<span title={branch} />}
                  className="flex-1 font-mono text-meta whitespace-nowrap text-ink-subtle @max-lg:hidden"
                >
                  {branch}
                </FadeText>
                <Tooltip>
                  <TooltipTrigger
                    render={
                      <Button
                        aria-label={`Branch: ${branch}`}
                        className="hidden text-ink-subtle @max-lg:inline-flex"
                        size="icon-sm"
                        variant="ghost"
                      />
                    }
                  >
                    <Icon icon={GitBranch} size="sm" />
                  </TooltipTrigger>
                  <TooltipContent>{branch}</TooltipContent>
                </Tooltip>
              </>
            )}
          </Inline>
        }
        actions={actions}
      />
    </Stack>
  )
}
