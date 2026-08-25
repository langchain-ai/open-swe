import { useCallback, useEffect, useMemo, useState } from "react"
import { DownloadIcon } from "lucide-react"

import type { AgentThread } from "@/features/agents/lib/types"
import { agentsApi } from "@/features/agents/lib/api"
import {
  useAgentThreadBranchDiff,
  useAgentThreadWorkingTreeDiff,
} from "@/features/agents/lib/queries"
import { ChangesPanel } from "@/features/agents/components/ChangesPanel"
import { toPanelFiles } from "@/features/agents/components/DiffFilesView"
import { AgentRightPanel } from "@/features/agents/components/panel/AgentRightPanel"
import {
  selectThreadDiffScope,
  useDiffPanelStore,
} from "@/features/agents/lib/diffPanelStore"
import {
  selectThreadRightPanelState,
  useRightPanelStore,
} from "@/features/agents/lib/rightPanelStore"
import { useTerminalGroups } from "@/features/agents/lib/terminalGroups"

interface AgentGitPanelProps {
  thread: AgentThread
  revealFilePath?: string | null
  revealChangesKey?: number
  collapsed: boolean
  onCollapsedChange: (next: boolean) => void
}

export function AgentGitPanel({
  thread,
  revealFilePath,
  revealChangesKey = 0,
  collapsed,
  onCollapsedChange,
}: AgentGitPanelProps) {
  const threadRef = useMemo(
    () => ({ scope: "cloud" as const, threadId: thread.id }),
    [thread.id]
  )
  const terminals = useTerminalGroups(
    { kind: "cloud", threadId: thread.id },
    ""
  )
  const openSurface = useRightPanelStore((state) => state.open)
  const activeSurfaceId = useRightPanelStore(
    (state) =>
      selectThreadRightPanelState(state.byThreadKey, threadRef).activeSurfaceId
  )
  useEffect(() => {
    if (revealChangesKey > 0) openSurface(threadRef, "diff")
  }, [openSurface, revealChangesKey, threadRef])

  const terminalAvailable =
    thread.isOwner !== false && Boolean(thread.sandboxId)

  // Served from GitHub, so it needs a repository — with or without a PR.
  const branchScopeAvailable =
    Boolean(thread.repoFullName) && Boolean(thread.branch)
  const selectScope = useDiffPanelStore((state) => state.selectScope)
  const scope = useDiffPanelStore((state) =>
    selectThreadDiffScope(
      state.byThreadKey,
      threadRef,
      branchScopeAvailable,
      Boolean(thread.pr)
    )
  )
  const diffVisible = !collapsed && activeSurfaceId === "diff"

  const turnDiff = useAgentThreadWorkingTreeDiff(
    thread.id,
    diffVisible && scope === "working-tree",
    thread.status === "running"
  )
  const branchDiff = useAgentThreadBranchDiff(
    thread.id,
    diffVisible && scope === "branch"
  )
  const diff =
    scope === "branch"
      ? {
          files: branchDiff.data?.files ?? [],
          // The branch endpoint answers from GitHub: a successful response is
          // always a real diff, and a failure surfaces through `error`.
          status: branchDiff.data ? ("ready" as const) : undefined,
          truncated: branchDiff.data?.truncated,
          isPending: branchDiff.isPending,
          isFetching: branchDiff.isFetching,
          error: branchDiff.error,
          refetch: branchDiff.refetch,
        }
      : {
          files: turnDiff.data?.files ?? [],
          status: turnDiff.data?.status,
          truncated: turnDiff.data?.truncated,
          isPending: turnDiff.isPending,
          isFetching: turnDiff.isFetching,
          error: turnDiff.error,
          refetch: turnDiff.refetch,
        }
  const files = useMemo(() => toPanelFiles(diff.files), [diff.files])

  // Refresh whenever the window regains focus: the diff is read live, so a
  // push or a review landing elsewhere should be visible on return.
  const refetchDiff = diff.refetch
  useEffect(() => {
    if (!diffVisible) return
    const onFocus = () => void refetchDiff()
    window.addEventListener("focus", onFocus)
    return () => window.removeEventListener("focus", onFocus)
  }, [diffVisible, refetchDiff])

  const [recoveringPatch, setRecoveringPatch] = useState(false)
  const [recoveryError, setRecoveryError] = useState<string | null>(null)
  const canDownloadRecovery =
    thread.status !== "running" && thread.isOwner !== false
  const downloadRecoveryPatch = useCallback(async () => {
    setRecoveringPatch(true)
    setRecoveryError(null)
    try {
      const { blob, filename } = await agentsApi.downloadThreadRecoveryPatch(
        thread.id
      )
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
    } catch (error) {
      setRecoveryError(
        error instanceof Error ? error.message : "Failed to download patch"
      )
    } finally {
      setRecoveringPatch(false)
    }
  }, [thread.id])

  return (
    <AgentRightPanel
      threadRef={threadRef}
      terminals={terminals}
      terminalTarget={{ kind: "cloud", threadId: thread.id }}
      cwd=""
      terminalAvailable={terminalAvailable}
      diffAvailable
      collapsed={collapsed}
      onCollapsedChange={onCollapsedChange}
      renderDiff={({ fullScreen }) => (
        <ChangesPanel
          files={files}
          status={diff.status}
          isLoading={diff.isPending}
          isFetching={diff.isFetching}
          error={diff.error}
          truncated={diff.truncated}
          branch={thread.branch}
          pr={thread.pr}
          revealFilePath={revealFilePath}
          fullScreen={fullScreen}
          onRefresh={() => void diff.refetch()}
          scope={scope}
          branchScopeAvailable={branchScopeAvailable}
          onScopeChange={(next) => selectScope(threadRef, next)}
          extraActions={
            canDownloadRecovery ? (
              <button
                type="button"
                aria-label="Download recovery patch"
                title={recoveryError ?? "Download recovery patch"}
                disabled={recoveringPatch}
                onClick={() => void downloadRecoveryPatch()}
                className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
              >
                <DownloadIcon className="size-3.5" />
              </button>
            ) : undefined
          }
        />
      )}
    />
  )
}
