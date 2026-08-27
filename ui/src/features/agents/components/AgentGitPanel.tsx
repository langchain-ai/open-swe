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
import {
  useLocalThreadDiff,
  useLocalThreadPrDiff,
} from "@/features/agents/lib/desktopLocal"
import {
  canRunThread,
  isLocalThread,
  useDeviceIdentity,
} from "@/features/agents/lib/runLocation"

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
  // A local thread's diff and terminals come from this machine over IPC, not
  // from the sandbox endpoints; everything else about the panel is the same.
  const deviceIdentity = useDeviceIdentity()
  // Only the owning machine can read this thread's working tree or open a
  // shell in it, so elsewhere the local surfaces are simply absent.
  const local = isLocalThread(thread) && canRunThread(thread, deviceIdentity.data?.deviceId)
  const cwd = local ? (thread.localProjectPath ?? "") : ""
  const threadRef = useMemo(
    () => ({
      scope: local ? ("local" as const) : ("cloud" as const),
      threadId: thread.id,
    }),
    [local, thread.id]
  )
  const terminalTarget = useMemo(
    () =>
      local
        ? ({ kind: "local" as const, sessionId: thread.id })
        : ({ kind: "cloud" as const, threadId: thread.id }),
    [local, thread.id]
  )
  const terminals = useTerminalGroups(terminalTarget, cwd)
  const openSurface = useRightPanelStore((state) => state.open)
  const activeSurfaceId = useRightPanelStore(
    (state) =>
      selectThreadRightPanelState(state.byThreadKey, threadRef).activeSurfaceId
  )
  useEffect(() => {
    if (revealChangesKey > 0) openSurface(threadRef, "diff")
  }, [openSurface, revealChangesKey, threadRef])

  const isRunning = thread.status === "running"
  const diffVisible = !collapsed && activeSurfaceId === "diff"
  const selectScope = useDiffPanelStore((state) => state.selectScope)

  // Also the source of the branch/PR metadata for a local thread, so it stays
  // enabled in either scope: it is what tells us the branch has a PR at all.
  const localCheckpointDiff = useLocalThreadDiff(
    thread.id,
    local && diffVisible,
    isRunning
  )
  const localRepository = localCheckpointDiff.data?.repository
  const branchScopeAvailable = local
    ? Boolean(localRepository?.pr)
    : // Served from GitHub, so it needs a repository — with or without a PR.
      Boolean(thread.repoFullName) && Boolean(thread.branch)
  const scope = useDiffPanelStore((state) =>
    selectThreadDiffScope(
      state.byThreadKey,
      threadRef,
      branchScopeAvailable,
      local ? undefined : Boolean(thread.pr)
    )
  )
  const localBranchDiff = useLocalThreadPrDiff(
    thread.id,
    local && diffVisible && scope === "branch",
    isRunning
  )
  const terminalAvailable = local || (!isLocalThread(thread) && Boolean(thread.sandboxId))

  const turnDiff = useAgentThreadWorkingTreeDiff(
    thread.id,
    !local && diffVisible && scope === "working-tree",
    isRunning
  )
  const branchDiff = useAgentThreadBranchDiff(
    thread.id,
    !local && diffVisible && scope === "branch"
  )
  const localDiffQuery = scope === "branch" ? localBranchDiff : localCheckpointDiff
  const diff = local
    ? {
        files: localDiffQuery.data?.files ?? [],
        status: localDiffQuery.data?.status,
        truncated: localDiffQuery.data?.truncated,
        isPending: localDiffQuery.isPending,
        isFetching: localDiffQuery.isFetching,
        error: localDiffQuery.error,
        refetch: localDiffQuery.refetch,
      }
    : scope === "branch"
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
  const branch = local
    ? (localRepository?.branch ?? thread.branch)
    : thread.branch
  const pr = local ? (localRepository?.pr ?? undefined) : thread.pr

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
  // The recovery patch is built from the thread's sandbox, which a local
  // thread does not have — its working tree is right there on disk.
  const canDownloadRecovery = !local && thread.status !== "running"
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
      terminalTarget={terminalTarget}
      cwd={cwd}
      terminalAvailable={terminalAvailable}
      diffAvailable={local || !isLocalThread(thread)}
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
          branch={branch}
          pr={pr}
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
