import { Navigate, createFileRoute } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"

import type { ReviewerEvalStatus } from "@/lib/api"
import { AppShell, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/admin_/evals")({ component: ReviewerEvalPage })

function ReviewerEvalPage() {
  const session = useSession()

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    )
  }
  if (!session.data) return <RequireLogin />
  if (!session.data.is_admin) return <Navigate to="/my-settings" />

  return (
    <AppShell
      user={session.data}
      title="Reviewer eval"
      description="Triggered from the Reviewer eval GitHub Action (run it on the prod branch). Progress streams here live."
      backTo={{ to: "/admin", label: "Back to Admin" }}
    >
      <ReviewerEvalStatusSection />
      <ReviewerEvalLogs />
    </AppShell>
  )
}

function useReviewerEvalStatus() {
  return useQuery({
    queryKey: ["reviewerEval"],
    queryFn: api.getReviewerEval,
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 5000 : false,
  })
}

function ReviewerEvalStatusSection() {
  const status = useReviewerEvalStatus()
  return (
    <SettingsSection
      title="Current run"
      description="Status and resolved configuration for the latest reviewer eval run."
    >
      <ReviewerEvalStatusView data={status.data ?? null} />
    </SettingsSection>
  )
}

function progressLabel(data: ReviewerEvalStatus): string | null {
  if (!data.progress) return null
  const { completed, total } = data.progress
  return `${completed} / ${total ?? "?"}`
}

function ReviewerEvalStatusView({ data }: { data: ReviewerEvalStatus | null }) {
  if (!data) {
    return (
      <div className="p-4 text-xs text-muted-foreground">
        Loading reviewer eval status…
      </div>
    )
  }

  const config = data.config_snapshot
  return (
    <div className="grid gap-2 p-4 text-xs text-muted-foreground sm:grid-cols-2">
      <StatusLine label="Status" value={data.status} strong />
      <StatusLine label="Progress" value={progressLabel(data)} />
      <StatusLine label="Run name" value={data.run_name ?? config?.experiment_prefix} />
      <StatusLine label="Dataset" value={config?.dataset_name} />
      <StatusLine label="Limit" value={data.limit ? String(data.limit) : "full dataset"} />
      <StatusLine label="Model" value={config?.model_id} />
      <StatusLine label="Effort" value={config?.reasoning_effort} />
      <StatusLine label="Score mode" value={config?.score_mode} />
      <StatusLine label="Threshold" value={config?.severity_threshold} />
      <StatusLine label="Cap" value={config ? String(config.cap) : null} />
      <StatusLine label="LangSmith project" value={data.langsmith_project} />
      <StatusLine label="Triggered by" value={data.created_by} />
      {data.started_at && (
        <StatusLine
          label="Started"
          value={new Date(data.started_at).toLocaleString()}
        />
      )}
      {data.finished_at && (
        <StatusLine
          label="Finished"
          value={new Date(data.finished_at).toLocaleString()}
        />
      )}
      {data.experiment_url && (
        <a
          href={data.experiment_url}
          target="_blank"
          rel="noreferrer"
          className="underline hover:text-foreground"
        >
          View experiment in LangSmith
        </a>
      )}
      {data.github_run_url && (
        <a
          href={data.github_run_url}
          target="_blank"
          rel="noreferrer"
          className="underline hover:text-foreground"
        >
          View GitHub run
        </a>
      )}
      {data.error && <span className="text-destructive">{data.error}</span>}
    </div>
  )
}

function StatusLine({
  label,
  value,
  strong = false,
}: {
  label: string
  value: string | null | undefined
  strong?: boolean
}) {
  return (
    <span>
      {label}:{" "}
      <span className={strong ? "font-medium text-foreground" : ""}>
        {value || "—"}
      </span>
    </span>
  )
}

function ReviewerEvalLogs() {
  const status = useReviewerEvalStatus()
  const logTail = status.data?.log_tail ?? null
  const running = status.data?.status === "running"

  const scrollRef = useRef<HTMLPreElement>(null)
  const [follow, setFollow] = useState(true)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (follow && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [logTail, follow])

  const copyLogs = async () => {
    if (!logTail) return
    await navigator.clipboard.writeText(logTail)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }

  return (
    <SettingsSection
      title="Output"
      description="Live tail of the eval output. Each PR logs start and finish lines while the run is active."
      action={
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => void copyLogs()}
            disabled={!logTail}
          >
            {copied ? "Copied" : "Copy logs"}
          </Button>
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={follow}
              onChange={(e) => setFollow(e.target.checked)}
            />
            Follow
          </label>
        </div>
      }
    >
      <div className="p-4">
        {logTail ? (
          <pre
            ref={scrollRef}
            onScroll={(e) => {
              const el = e.currentTarget
              const atBottom =
                el.scrollHeight - el.scrollTop - el.clientHeight < 24
              setFollow(atBottom)
            }}
            className="max-h-[28rem] overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/50 p-3 font-mono text-xs text-foreground"
          >
            {logTail}
          </pre>
        ) : (
          <p className="text-xs text-muted-foreground">
            {running
              ? "Waiting for output…"
              : "No output yet. Trigger the Reviewer eval GitHub Action to see logs here."}
          </p>
        )}
      </div>
    </SettingsSection>
  )
}
