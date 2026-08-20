import { useCallback, useEffect, useMemo, useState } from "react"
import { DownloadIcon } from "lucide-react"

import type { AgentThread } from "@/features/agents/lib/types"
import { agentsApi } from "@/features/agents/lib/api"
import { useAgentThreadTurnDiff } from "@/features/agents/lib/queries"
import { ChangesPanel } from "@/features/agents/components/ChangesPanel"
import { toPanelFiles } from "@/features/agents/components/DiffFilesView"
import { AgentRightPanel } from "@/features/agents/components/panel/AgentRightPanel"
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

  const turnDiff = useAgentThreadTurnDiff(
    thread.id,
    null,
    !collapsed && activeSurfaceId === "diff",
    {},
    thread.status === "running"
  )
  const files = useMemo(
    () => toPanelFiles(turnDiff.data?.files ?? []),
    [turnDiff.data?.files]
  )
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
      pullRequests={thread.pullRequests ?? []}
      collapsed={collapsed}
      onCollapsedChange={onCollapsedChange}
      renderDiff={({ fullScreen }) => (
        <ChangesPanel
          files={files}
          status={turnDiff.data?.status}
          isLoading={turnDiff.isPending}
          isFetching={turnDiff.isFetching}
          error={turnDiff.error}
          truncated={turnDiff.data?.truncated}
          branch={thread.branch}
          pr={thread.pr}
          revealFilePath={revealFilePath}
          fullScreen={fullScreen}
          onRefresh={() => void turnDiff.refetch()}
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
