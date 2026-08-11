import { useCallback, useMemo, useState } from "react"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"
import { useQueryClient } from "@tanstack/react-query"

import type { AgentThread } from "@/features/agents/lib/types"
import { agentsApi } from "@/features/agents/lib/api"
import {
  agentThreadKeys,
  invalidateAgentThreadLists,
  useAgentThreadPrDiff,
  useAgentThreadTurnDiff,
} from "@/features/agents/lib/queries"
import { ReviewTab } from "@/features/reviews/components/ReviewTab"
import { PrHeader } from "@/features/reviews/components/PrHeader"
import { buttonVariants } from "@/components/ui/button"
import { AgentPanelShell } from "@/features/agents/components/AgentPanelShell"
import {
  DiffFilesView,
  toPanelFiles,
} from "@/features/agents/components/DiffFilesView"
import { PlanView } from "@/features/agents/components/PlanView"
import { cn } from "@/lib/utils"

export type AgentPanelTab = "git" | "plan"

interface AgentGitPanelProps {
  thread: AgentThread
  /** Path to select and scroll to, set when a transcript row is clicked. */
  revealFilePath?: string | null
  collapsed: boolean
  requestedTab: AgentPanelTab
  onCollapsedChange: (next: boolean) => void
  onTabChange: (tab: AgentPanelTab) => void
}

export function AgentGitPanel({
  thread,
  revealFilePath,
  collapsed,
  requestedTab,
  onCollapsedChange,
  onTabChange,
}: AgentGitPanelProps) {
  const queryClient = useQueryClient()
  const stream = useAgentThreadStream()
  const [tab, setTab] = useState<"diff" | "review" | "commits">("diff")
  const hasPlan = Boolean(
    thread.planStatus &&
    thread.planStatus !== "approved" &&
    thread.planStatus !== "cancelled"
  )

  const topTab = hasPlan || requestedTab !== "plan" ? requestedTab : "git"
  const onPlanApproved = useCallback(
    (runId: string) => {
      queryClient.setQueryData<AgentThread>(
        agentThreadKeys.detail(thread.id),
        (current) =>
          current
            ? { ...current, planStatus: "approved", status: "running" }
            : current
      )
      void queryClient.invalidateQueries({ queryKey: ["plan", thread.id] })
      invalidateAgentThreadLists(queryClient)
      onTabChange("git")
      void stream.client.runs.join(thread.id, runId).finally(() => {
        void queryClient.invalidateQueries({
          queryKey: agentThreadKeys.detail(thread.id),
        })
      })
    },
    [onTabChange, queryClient, stream, thread.id]
  )

  // Collapsed state is owned by the parent (so the plan banner can reserve space
  // for the floating expand button); persistence to localStorage lives there too.
  const setCollapsed = onCollapsedChange

  const pr = thread.pr

  // The open/closed state is persisted to localStorage, so it carries across
  // threads and reloads. Still uncollapse when a PR lands mid-session.
  const [prSeen, setPrSeen] = useState<{ threadId: string; hadPr: boolean }>(
    () => ({ threadId: thread.id, hadPr: Boolean(pr) })
  )
  if (prSeen.threadId !== thread.id) {
    setPrSeen({ threadId: thread.id, hadPr: Boolean(pr) })
  } else if (pr && !prSeen.hadPr) {
    setPrSeen({ threadId: thread.id, hadPr: true })
    setCollapsed(false)
  }

  const prDiff = useAgentThreadPrDiff(thread.id, Boolean(pr))
  // Without a PR the sandbox's git checkpoints are the only source of truth for
  // what this thread changed.
  const turnDiff = useAgentThreadTurnDiff(thread.id, null, !pr && !collapsed)
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

  const files = useMemo(
    () => toPanelFiles(prDiff.data?.files ?? turnDiff.data?.files ?? []),
    [prDiff.data, turnDiff.data]
  )

  const tabs = (
    [
      ["diff", "Diff"],
      ["review", "Review"],
      ["commits", "Commits"],
    ] as const
  ).map(([id, label]) => (
    <button
      key={id}
      type="button"
      onClick={() => setTab(id)}
      className={cn(
        "rounded-md px-2.5 py-1 text-xs transition-colors",
        tab === id
          ? "bg-accent font-medium text-foreground"
          : "text-muted-foreground/70 hover:bg-accent"
      )}
    >
      {label}
    </button>
  ))

  const actions = (
    <>
      {recoveryError && (
        <span
          title={recoveryError}
          className="max-w-40 truncate text-[11px] text-destructive"
        >
          {recoveryError}
        </span>
      )}
      {canDownloadRecovery && (
        <button
          type="button"
          onClick={downloadRecoveryPatch}
          disabled={recoveringPatch}
          className={cn(
            buttonVariants({ variant: "outline", size: "sm" }),
            "h-7 px-2 text-[11px]"
          )}
        >
          {recoveringPatch ? "Preparing…" : "Download patch"}
        </button>
      )}
    </>
  )

  return (
    <AgentPanelShell
      tabs={[
        { id: "git", kind: "review" as const },
        ...(hasPlan ? [{ id: "plan", kind: "plan" as const }] : []),
      ]}
      activeTabId={topTab}
      onSelectTab={(id) => onTabChange(id as AgentPanelTab)}
      menuKinds={[]}
      collapsed={collapsed}
      onCollapsedChange={setCollapsed}
    >
      {({ fullScreen }) => (
        <>
          {topTab === "plan" ? (
            <PlanView threadId={thread.id} onApprove={onPlanApproved} />
          ) : (
            <>
              {pr && (
                <PrHeader
                  className="border-b border-border px-4 py-3"
                  url={pr.url}
                  title={pr.title}
                  number={pr.number}
                  state={pr.state}
                  headRef={pr.headRef}
                  baseRef={pr.baseRef}
                  titleClassName="truncate text-sm"
                />
              )}

              {tab === "diff" ? (
                <DiffFilesView
                  files={files}
                  revealFilePath={revealFilePath}
                  fullScreen={fullScreen}
                  emptyLabel={
                    prDiff.isLoading ? "Loading PR diff…" : "No diff available."
                  }
                  truncated={prDiff.data?.truncated ?? turnDiff.data?.truncated}
                  leading={tabs}
                  actions={actions}
                />
              ) : (
                <>
                  <div className="flex items-center gap-1 border-b border-border px-3 py-2">
                    {tabs}
                    <div className="ml-auto flex min-w-0 items-center gap-2">
                      {actions}
                    </div>
                  </div>
                  <div className="flex min-h-0 flex-1">
                    {tab === "review" ? (
                      <ReviewTab thread={thread} />
                    ) : (
                      <div className="min-h-0 flex-1 overflow-y-auto p-6 text-center text-xs text-muted-foreground/70">
                        Coming Soon
                      </div>
                    )}
                  </div>
                </>
              )}
            </>
          )}
        </>
      )}
    </AgentPanelShell>
  )
}
