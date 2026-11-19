import { PlanView } from "@/features/agents/components/PlanView"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { DownloadSimpleIcon } from "@phosphor-icons/react/dist/ssr/DownloadSimple"

import type {
  AgentThread,
  Message,
  ToolExecutionChunk,
} from "@/features/agents/lib/types"
import { useThreadSource } from "@/features/agents/lib/threadSource/ThreadSourceProvider"
import { useSession } from "@/lib/session"
import { agentsApi } from "@/features/agents/lib/api"
import {
  useAgentThreadBranchDiff,
  useAgentThreadWorkingTreeDiff,
} from "@/features/agents/lib/queries"
import { ChangesPanel } from "@/features/agents/components/ChangesPanel"
import { toPanelFiles } from "@/features/agents/components/DiffFilesView"
import { AgentRightPanel } from "@/features/agents/components/panel/AgentRightPanel"
import { BrowserPanel } from "@/features/agents/components/BrowserPanel"
import { ThreadAppPreview } from "@/features/plugins/components/ThreadAppPreview"
import { useAppPreview } from "@/features/plugins/lib/queries"
import {
  BackgroundWorkPanel,
  useHasBackgroundWork,
} from "@/features/agents/components/BackgroundWorkPanel"
import {
  selectThreadDiffScope,
  useDiffPanelStore,
} from "@/features/agents/lib/diffPanelStore"
import {
  selectThreadRightPanelState,
  useRightPanelStore,
} from "@/features/agents/lib/rightPanelStore"
import { useTerminalGroups } from "@/features/agents/lib/terminalGroups"
import { normalizeSelection } from "@/features/agents/utils/passageReply"
import type { PassageReply } from "@/features/agents/utils/passageReply"

interface AgentGitPanelProps {
  onComment?: (content: string) => Promise<void>
  /** Takes replies to text selected in the panel's files and terminals. */
  onReply?: (reply: PassageReply) => void
  thread: AgentThread
  revealFilePath?: string | null
  revealChangesKey?: number
  collapsed: boolean
  onCollapsedChange: (next: boolean) => void
}

/** Completed file edits in a thread, including those an `eval` call's code made. */
function completedEdits(messages: Array<Message>): number {
  const count = (calls: Array<ToolExecutionChunk>): number =>
    calls.reduce(
      (total, call) =>
        total +
        (call.toolKind === "edit" && call.status === "completed" ? 1 : 0) +
        count(call.calls ?? []),
      0
    )
  return count(
    messages.flatMap((message) =>
      message.chunks.filter(
        (chunk): chunk is ToolExecutionChunk => chunk.kind === "tool-execution"
      )
    )
  )
}

export function AgentGitPanel({
  thread,
  onComment,
  onReply,
  revealFilePath,
  revealChangesKey = 0,
  collapsed,
  onCollapsedChange,
}: AgentGitPanelProps) {
  const threadRef = useMemo(
    () => ({ scope: "cloud" as const, threadId: thread.id }),
    [thread.id]
  )
  const terminalTarget = useMemo(() => ({ threadId: thread.id }), [thread.id])
  const terminals = useTerminalGroups(terminalTarget)
  const openSurface = useRightPanelStore((state) => state.open)
  const activeSurfaceId = useRightPanelStore(
    (state) =>
      selectThreadRightPanelState(state.byThreadKey, threadRef).activeSurfaceId
  )
  useEffect(() => {
    if (revealChangesKey > 0) openSurface(threadRef, "diff")
  }, [openSurface, revealChangesKey, threadRef])

  const terminalAvailable = Boolean(thread.sandboxId)

  // The browser can be opened once the agent's first run names it, and its
  // panel shows on its own when it opens, once per thread view.
  const browserAvailable = Boolean(thread.browser)
  const browserOpened = thread.browser === "opened"
  const browserShownRef = useRef(browserOpened)
  useEffect(() => {
    if (!browserOpened || browserShownRef.current) return
    browserShownRef.current = true
    openSurface(threadRef, "preview")
    onCollapsedChange(false)
  }, [browserOpened, onCollapsedChange, openSurface, threadRef])

  const backgroundWorkAvailable = useHasBackgroundWork(thread.id)

  // The app the agent builds shows on its own each time it previews a build.
  const appPreview = useAppPreview(thread.id)
  const previewed = appPreview.data
    ? (appPreview.data.running?.version.id ??
      appPreview.data.waiting?.id ??
      null)
    : undefined
  const previewSeenRef = useRef<string | null | undefined>(undefined)
  useEffect(() => {
    if (previewed === undefined) return
    const seen = previewSeenRef.current
    previewSeenRef.current = previewed
    if (seen === undefined || previewed === null || previewed === seen) return
    openSurface(threadRef, "app")
    onCollapsedChange(false)
  }, [onCollapsedChange, openSurface, previewed, threadRef])

  const isRunning = thread.status === "running"
  const diffVisible = !collapsed && activeSurfaceId === "diff"
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

  const turnDiff = useAgentThreadWorkingTreeDiff(
    thread.id,
    diffVisible && scope === "working-tree",
    isRunning
  )
  const branchDiff = useAgentThreadBranchDiff(
    thread.id,
    diffVisible && scope === "branch"
  )
  const source = useThreadSource()
  const session = useSession()
  const autoShowChanges = session.data?.auto_show_changes ?? true
  const running = isRunning || source.isRunning
  const hydrating = source.isHydrating
  const edits = completedEdits(source.messages)
  const lastSeenRun = useRef({ running, hydrating: true, editsAtStart: 0 })
  const refetchBranch = branchDiff.refetch
  const refetchWorkingTree = turnDiff.refetch
  useEffect(() => {
    const previous = lastSeenRun.current
    const tracking = previous.running && !previous.hydrating
    lastSeenRun.current = {
      running,
      hydrating,
      editsAtStart: tracking ? previous.editsAtStart : edits,
    }
    if (
      !tracking ||
      running ||
      !autoShowChanges ||
      !collapsed ||
      edits <= previous.editsAtStart
    )
      return

    let cancelled = false
    void (async () => {
      const branchResult = await refetchBranch()
      let nextScope: "branch" | "working-tree" = "branch"
      if (!branchResult.isSuccess || !branchResult.data.files.length) {
        const treeResult = await refetchWorkingTree()
        if (
          !treeResult.isSuccess ||
          treeResult.data.status !== "ready" ||
          !treeResult.data.files.length
        )
          return
        nextScope = "working-tree"
      }
      if (cancelled) return
      selectScope(threadRef, nextScope)
      openSurface(threadRef, "diff")
      onCollapsedChange(false)
    })()
    return () => {
      cancelled = true
    }
  }, [
    activeSurfaceId,
    autoShowChanges,
    collapsed,
    edits,
    hydrating,
    onCollapsedChange,
    openSurface,
    refetchBranch,
    refetchWorkingTree,
    running,
    selectScope,
    threadRef,
  ])

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
  const canDownloadRecovery = !isRunning
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
      terminalAvailable={terminalAvailable}
      diffAvailable
      {...(thread.planStatus
        ? { renderArtifact: () => <PlanView threadId={thread.id} embedded /> }
        : {})}
      browserAvailable={browserAvailable}
      renderBrowser={() => (
        <BrowserPanel threadId={thread.id} opened={browserOpened} />
      )}
      backgroundWorkAvailable={backgroundWorkAvailable}
      renderBackgroundWork={() => <BackgroundWorkPanel threadId={thread.id} />}
      {...(previewed
        ? {
            renderApp: () => (
              <ThreadAppPreview
                threadId={thread.id}
                active={!collapsed && activeSurfaceId === "app"}
              />
            ),
          }
        : {})}
      collapsed={collapsed}
      onCollapsedChange={onCollapsedChange}
      onReply={onReply}
      running={isRunning}
      {...(onReply && {
        // The terminal hands over only the selected text, so there is no context to add.
        onTerminalAddToChat: (text: string) =>
          onReply({
            source: "terminal",
            before: "",
            selection: normalizeSelection(text),
            after: "",
            comment: "",
          }),
      })}
      renderDiff={({ fullScreen }) => (
        <ChangesPanel
          files={files}
          onComment={onComment}
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
              <IconButton
                icon={DownloadSimpleIcon}
                label="Download recovery patch"
                tooltipProps={
                  recoveryError ? { title: recoveryError } : undefined
                }
                size="sm"
                color="secondary"
                variant="plain"
                loading={recoveringPatch}
                disabled={recoveringPatch}
                onClick={() => void downloadRecoveryPatch()}
              />
            ) : undefined
          }
        />
      )}
    />
  )
}
