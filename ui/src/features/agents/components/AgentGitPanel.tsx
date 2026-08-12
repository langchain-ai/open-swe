import { useCallback, useMemo, useState } from "react"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"
import { useQueryClient } from "@tanstack/react-query"
import {
  CheckIcon,
  ChevronDownIcon,
  DownloadIcon,
  EllipsisIcon,
  GitPullRequestIcon,
  RefreshCwIcon,
  TextAlignStartIcon,
} from "lucide-react"

import type { AgentThread } from "@/features/agents/lib/types"
import { agentsApi } from "@/features/agents/lib/api"
import {
  agentThreadKeys,
  invalidateAgentThreadLists,
  useAgentThreadPrDiff,
  useAgentThreadTurnDiff,
} from "@/features/agents/lib/queries"
import { ReviewTab } from "@/features/reviews/components/ReviewTab"
import { AgentPanelShell } from "@/features/agents/components/AgentPanelShell"
import {
  Menu,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuTrigger,
} from "@/components/ui/menu"
import { useDiffWrap } from "@/features/agents/utils/diffUtils"
import {
  DiffFilesView,
  toPanelFiles,
} from "@/features/agents/components/DiffFilesView"
import { PlanView } from "@/features/agents/components/PlanView"

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
  const [wrap, setWrap] = useDiffWrap()
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

  const totals = useMemo(
    () =>
      files.reduce(
        (sum, file) => ({
          additions: sum.additions + file.additions,
          deletions: sum.deletions + file.deletions,
        }),
        { additions: 0, deletions: 0 }
      ),
    [files]
  )
  const headRef = pr?.headRef ?? thread.branch
  const baseRef = pr?.baseRef ?? "main"
  const tabLabels = { diff: "Branch", review: "Review", commits: "Committed" }
  const refreshDiff = () => void (pr ? prDiff.refetch() : turnDiff.refetch())

  const reviewHeader = (
    <div className="shrink-0 px-3 pb-2">
      <div className="flex min-h-9 items-center gap-2">
        <Menu>
          <MenuTrigger className="flex items-center gap-1 rounded-md px-2 py-1 text-sm font-medium text-foreground transition-colors hover:bg-accent">
            {tabLabels[tab]}
            <ChevronDownIcon className="size-3.5 text-muted-foreground" />
          </MenuTrigger>
          <MenuPopup align="start" className="w-44">
            {(
              [
                ["diff", "Branch"],
                ["review", "Review"],
                ["commits", "Committed"],
              ] as const
            ).map(([id, label]) => (
              <MenuItem key={id} onClick={() => setTab(id)}>
                <span className="flex-1">{label}</span>
                {tab === id && <CheckIcon />}
              </MenuItem>
            ))}
          </MenuPopup>
        </Menu>
        {files.length > 0 && (
          <span className="flex items-center gap-2 text-sm">
            <span className="text-success-foreground">+{totals.additions}</span>
            <span className="text-destructive">-{totals.deletions}</span>
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          {recoveryError && (
            <span
              title={recoveryError}
              className="max-w-32 truncate text-[11px] text-destructive"
            >
              {recoveryError}
            </span>
          )}
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
          <Menu>
            <MenuTrigger
              aria-label="Review options"
              className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <EllipsisIcon className="size-4" />
            </MenuTrigger>
            <MenuPopup align="end" className="w-48">
              <MenuItem onClick={refreshDiff}>
                <RefreshCwIcon />
                Refresh
              </MenuItem>
              <MenuItem onClick={() => setWrap(!wrap)}>
                <TextAlignStartIcon />
                {wrap ? "Disable" : "Enable"} word wrap
              </MenuItem>
              {canDownloadRecovery && (
                <>
                  <MenuSeparator />
                  <MenuItem
                    disabled={recoveringPatch}
                    onClick={downloadRecoveryPatch}
                  >
                    <DownloadIcon />
                    {recoveringPatch ? "Preparing…" : "Download patch"}
                  </MenuItem>
                </>
              )}
            </MenuPopup>
          </Menu>
        </div>
      </div>
    </div>
  )

  return (
    <AgentPanelShell
      tabs={[
        { id: "git", kind: "review" as const },
        ...(hasPlan ? [{ id: "plan", kind: "plan" as const }] : []),
      ]}
      activeTabId={topTab}
      onSelectTab={(id) => onTabChange(id as AgentPanelTab)}
      onCloseTab={() => setCollapsed(true)}
      menuKinds={[]}
      collapsed={collapsed}
      onCollapsedChange={setCollapsed}
      seamlessHeader={topTab === "git"}
    >
      {({ fullScreen }) => (
        <>
          {topTab === "plan" ? (
            <PlanView threadId={thread.id} onApprove={onPlanApproved} />
          ) : (
            <>
              {reviewHeader}
              {tab === "diff" ? (
                <DiffFilesView
                  files={files}
                  revealFilePath={revealFilePath}
                  fullScreen={fullScreen}
                  leading={
                    <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
                      <span
                        className="min-w-0 truncate font-mono"
                        title={headRef}
                      >
                        {headRef}
                      </span>
                      <span className="shrink-0">→</span>
                      <span
                        className="min-w-0 truncate font-mono"
                        title={baseRef}
                      >
                        {baseRef}
                      </span>
                    </div>
                  }
                  emptyLabel={
                    prDiff.isLoading ? "Loading PR diff…" : "No diff available."
                  }
                  truncated={prDiff.data?.truncated ?? turnDiff.data?.truncated}
                />
              ) : (
                <div className="flex min-h-0 flex-1">
                  {tab === "review" ? (
                    <ReviewTab thread={thread} />
                  ) : (
                    <div className="min-h-0 flex-1 overflow-y-auto p-6 text-center text-xs text-muted-foreground/70">
                      Coming Soon
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </>
      )}
    </AgentPanelShell>
  )
}
