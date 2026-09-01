import { useMemo } from "react"

import { AgentRightPanel } from "@/features/agents/components/panel/AgentRightPanel"
import { ChangesPanel } from "@/features/agents/components/ChangesPanel"
import { toPanelFiles } from "@/features/agents/components/DiffFilesView"
import {
  selectThreadRightPanelState,
  useRightPanelStore,
} from "@/features/agents/lib/rightPanelStore"
import { useProjectDiff } from "@/features/agents/lib/desktopLocal"
import { useTerminalGroups } from "@/features/agents/lib/terminalGroups"

/**
 * Right panel for the new-thread screen on This Mac. The project directory is
 * already there, so terminals and worktree changes work before a thread exists;
 * both are scoped to the project rather than to a thread.
 */
export function LocalProjectRightPanel({
  scopeId,
  cwd,
  collapsed,
  onCollapsedChange,
}: {
  scopeId: string
  cwd: string
  collapsed: boolean
  onCollapsedChange: (next: boolean) => void
}) {
  const threadRef = useMemo(
    () => ({ scope: "local" as const, threadId: scopeId }),
    [scopeId]
  )
  const terminals = useTerminalGroups(
    { kind: "local", sessionId: scopeId },
    cwd
  )
  const activeSurfaceId = useRightPanelStore(
    (state) =>
      selectThreadRightPanelState(state.byThreadKey, threadRef).activeSurfaceId
  )
  const diff = useProjectDiff(cwd, !collapsed && activeSurfaceId === "diff")
  const files = useMemo(
    () => toPanelFiles(diff.data?.files ?? []),
    [diff.data?.files]
  )

  return (
    <AgentRightPanel
      threadRef={threadRef}
      terminals={terminals}
      terminalTarget={{ kind: "local", sessionId: scopeId }}
      cwd={cwd}
      terminalAvailable
      diffAvailable
      collapsed={collapsed}
      onCollapsedChange={onCollapsedChange}
      renderDiff={({ fullScreen }) => (
        <ChangesPanel
          files={files}
          status={diff.data?.status}
          isLoading={diff.isPending}
          isFetching={diff.isFetching}
          error={diff.error}
          truncated={diff.data?.truncated}
          branch={diff.data?.repository?.branch}
          pr={diff.data?.repository?.pr}
          fullScreen={fullScreen}
          onRefresh={() => void diff.refetch()}
          scope="working-tree"
          branchScopeAvailable={false}
          onScopeChange={() => {}}
        />
      )}
    />
  )
}
