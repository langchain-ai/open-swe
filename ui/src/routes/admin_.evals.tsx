import { Navigate, createFileRoute } from "@tanstack/react-router"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"

import type { ReviewerEvalStatus } from "@/lib/api"
import { AppShell, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
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
  if (!session.data) return <Navigate to="/login" />
  if (!session.data.is_admin) return <Navigate to="/my-settings" />

  return (
    <AppShell
      user={session.data}
      title="Reviewer eval"
      description="Run the offline reviewer benchmark against the LangSmith dataset and watch its output stream live."
      backTo={{ to: "/admin", label: "Back to Admin" }}
    >
      <ReviewerEvalRunner />
      <ReviewerEvalLogs />
    </AppShell>
  )
}

function ReviewerEvalRunner() {
  const qc = useQueryClient()
  const [limit, setLimit] = useState("")
  const [error, setError] = useState<string | null>(null)

  const status = useQuery({
    queryKey: ["reviewerEval"],
    queryFn: api.getReviewerEval,
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 5000 : false,
  })

  const data = status.data
  const running = data?.status === "running"

  const onSuccess = (next: ReviewerEvalStatus) => {
    qc.setQueryData(["reviewerEval"], next)
    setError(null)
  }
  const onError = (e: Error) => setError(e.message)

  const start = useMutation({
    mutationFn: () => {
      const n = limit.trim() ? Number(limit.trim()) : null
      if (n !== null && (!Number.isInteger(n) || n <= 0)) {
        throw new Error("Limit must be a positive whole number")
      }
      return api.startReviewerEval(n)
    },
    onSuccess,
    onError,
  })
  const cancel = useMutation({
    mutationFn: () => api.cancelReviewerEval(),
    onSuccess,
    onError,
  })

  return (
    <SettingsSection
      title="Run"
      description="Traces are sent to the open-swe-evals project. Leave the limit blank for the full dataset, or enter N to run only the first N PRs (smoke test)."
    >
      <div className="flex flex-col gap-3 p-4">
        <div className="flex items-center gap-2">
          <Input
            className="w-48"
            type="number"
            min={1}
            placeholder="Limit (optional)"
            value={limit}
            disabled={running}
            onChange={(e) => setLimit(e.target.value)}
          />
          <Button
            size="sm"
            onClick={() => start.mutate()}
            disabled={running || start.isPending}
          >
            {start.isPending ? "Starting…" : "Run eval"}
          </Button>
          {running && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => cancel.mutate()}
              disabled={cancel.isPending}
            >
              Cancel
            </Button>
          )}
        </div>

        {data && (
          <div className="flex flex-col gap-1 text-xs text-muted-foreground">
            <span>
              Status: <span className="font-medium">{data.status}</span>
              {data.langsmith_project ? ` · ${data.langsmith_project}` : ""}
              {data.limit ? ` · limit ${data.limit}` : ""}
            </span>
            {data.started_at && (
              <span>Started: {new Date(data.started_at).toLocaleString()}</span>
            )}
            {data.finished_at && (
              <span>
                Finished: {new Date(data.finished_at).toLocaleString()}
              </span>
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
            {data.error && <span className="text-destructive">{data.error}</span>}
          </div>
        )}

        {error && <p className="text-xs text-destructive">{error}</p>}
      </div>
    </SettingsSection>
  )
}

function ReviewerEvalLogs() {
  const status = useQuery({ queryKey: ["reviewerEval"], queryFn: api.getReviewerEval })
  const logTail = status.data?.log_tail ?? null
  const running = status.data?.status === "running"

  const scrollRef = useRef<HTMLPreElement>(null)
  const [follow, setFollow] = useState(true)

  useEffect(() => {
    if (follow && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [logTail, follow])

  return (
    <SettingsSection
      title="Output"
      description="Last 4000 characters of the eval process output. Updates roughly every 10 seconds while running."
      action={
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={follow}
            onChange={(e) => setFollow(e.target.checked)}
          />
          Follow
        </label>
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
            {running ? "Waiting for output…" : "No output yet. Run an eval to see logs here."}
          </p>
        )}
      </div>
    </SettingsSection>
  )
}
