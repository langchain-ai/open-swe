import { Navigate, createFileRoute } from "@tanstack/react-router"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"

import type {
  ModelOption,
  ReviewerEvalConfig,
  ReviewerEvalScoreMode,
  ReviewerEvalSeverity,
  ReviewerEvalStartRequest,
  ReviewerEvalStatus,
} from "@/lib/api"
import { AppShell, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

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

const DEFAULT_REVIEWER_EVAL_CONFIG: ReviewerEvalConfig = {
  dataset_name: "openswe-reviewer-v1",
  experiment_prefix: "openswe-review-confidence",
  max_concurrency: 5,
  langsmith_project: "open-swe-evals",
  langgraph_url: "",
  assistant_id: "reviewer",
  model_id: "google_genai:gemini-3.5-flash",
  reasoning_effort: "medium",
  score_mode: "all_findings",
  severity_threshold: "medium",
  cap: 4,
}

interface ReviewerEvalFormState {
  dataset_name: string
  experiment_prefix: string
  max_concurrency: string
  langsmith_project: string
  langgraph_url: string
  assistant_id: string
  model_id: string
  reasoning_effort: string
  score_mode: ReviewerEvalScoreMode
  severity_threshold: ReviewerEvalSeverity
  cap: string
  limit: string
}

function formFromConfig(
  config: ReviewerEvalConfig,
  limit: number | null = null
): ReviewerEvalFormState {
  return {
    dataset_name: config.dataset_name,
    experiment_prefix: config.experiment_prefix,
    max_concurrency: String(config.max_concurrency),
    langsmith_project: config.langsmith_project,
    langgraph_url: config.langgraph_url,
    assistant_id: config.assistant_id,
    model_id: config.model_id,
    reasoning_effort: config.reasoning_effort,
    score_mode: config.score_mode,
    severity_threshold: config.severity_threshold,
    cap: String(config.cap),
    limit: limit ? String(limit) : "",
  }
}

function parsePositiveInt(label: string, value: string): number {
  const n = Number(value.trim())
  if (!Number.isInteger(n) || n <= 0) {
    throw new Error(`${label} must be a positive whole number`)
  }
  return n
}

function parseOptionalPositiveInt(label: string, value: string): number | null {
  return value.trim() ? parsePositiveInt(label, value) : null
}

function parseNonNegativeInt(label: string, value: string): number {
  const n = Number(value.trim())
  if (!Number.isInteger(n) || n < 0) {
    throw new Error(`${label} must be a non-negative whole number`)
  }
  return n
}

function requireText(label: string, value: string): string {
  const text = value.trim()
  if (!text) throw new Error(`${label} is required`)
  return text
}

function FieldGroup({
  label,
  description,
  children,
}: {
  label: string
  description?: string
  children: ReactNode
}) {
  return (
    <div className="px-4 py-4">
      <div className="mb-3 flex flex-col gap-0.5">
        <span className="text-xs font-medium text-foreground">{label}</span>
        {description && (
          <span className="text-xs text-muted-foreground">{description}</span>
        )}
      </div>
      {children}
    </div>
  )
}

function Field({
  label,
  className,
  children,
}: {
  label: string
  className?: string
  children: ReactNode
}) {
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      {children}
    </div>
  )
}

function ReviewerEvalRunner() {
  const qc = useQueryClient()
  const [draft, setDraft] = useState<ReviewerEvalFormState>(() =>
    formFromConfig(DEFAULT_REVIEWER_EVAL_CONFIG)
  )
  const [error, setError] = useState<string | null>(null)
  const initialized = useRef(false)

  const status = useQuery({
    queryKey: ["reviewerEval"],
    queryFn: api.getReviewerEval,
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 5000 : false,
  })
  const options = useQuery({ queryKey: ["options"], queryFn: api.options })

  const data = status.data
  const running = data?.status === "running"
  const currentModel: ModelOption | undefined =
    options.data?.models.find((m) => m.id === draft.model_id) ??
    options.data?.models[0]

  useEffect(() => {
    if (initialized.current || !data?.config_snapshot) return
    initialized.current = true
    setDraft(formFromConfig(data.config_snapshot, data.limit))
  }, [data?.config_snapshot, data?.limit])

  useEffect(() => {
    if (!currentModel) return
    if (currentModel.id !== draft.model_id) {
      setDraft((current) => ({
        ...current,
        model_id: currentModel.id,
        reasoning_effort: currentModel.default_effort,
      }))
      return
    }
    if (!currentModel.efforts.includes(draft.reasoning_effort)) {
      setDraft((current) => ({
        ...current,
        reasoning_effort: currentModel.default_effort,
      }))
    }
  }, [currentModel, draft.model_id, draft.reasoning_effort])

  const setField = <TKey extends keyof ReviewerEvalFormState>(
    key: TKey,
    value: ReviewerEvalFormState[TKey]
  ) => {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  const buildRequest = (): ReviewerEvalStartRequest => {
    return {
      dataset_name: requireText("Dataset", draft.dataset_name),
      experiment_prefix: requireText("Run name", draft.experiment_prefix),
      max_concurrency: parsePositiveInt("Max concurrency", draft.max_concurrency),
      langsmith_project: requireText("LangSmith project", draft.langsmith_project),
      langgraph_url: draft.langgraph_url.trim(),
      assistant_id: requireText("Assistant ID", draft.assistant_id),
      model_id: requireText("Model", draft.model_id),
      reasoning_effort: requireText("Effort", draft.reasoning_effort),
      score_mode: draft.score_mode,
      severity_threshold: draft.severity_threshold,
      cap: parseNonNegativeInt("Cap", draft.cap),
      limit: parseOptionalPositiveInt("Limit", draft.limit),
    }
  }

  const onSuccess = (next: ReviewerEvalStatus) => {
    qc.setQueryData(["reviewerEval"], next)
    setError(null)
  }
  const onError = (e: Error) => setError(e.message)

  const start = useMutation({
    mutationFn: () => {
      return api.startReviewerEval(buildRequest())
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
    <>
      <SettingsSection
        title="Run configuration"
        description="Configure one reviewer eval run. These values override config.toml for this dashboard-triggered run without editing the file."
      >
        <div className="divide-y divide-border">
          <FieldGroup
            label="Run details"
            description="Run name maps to the LangSmith experiment prefix. Leave limit blank for the full dataset."
          >
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Run name">
                <Input
                  className="w-full"
                  placeholder="openswe-review-confidence"
                  value={draft.experiment_prefix}
                  disabled={running}
                  onChange={(e) => setField("experiment_prefix", e.target.value)}
                />
              </Field>
              <Field label="Dataset">
                <Input
                  className="w-full"
                  placeholder="openswe-reviewer-v1"
                  value={draft.dataset_name}
                  disabled={running}
                  onChange={(e) => setField("dataset_name", e.target.value)}
                />
              </Field>
              <Field label="Limit">
                <Input
                  className="w-full"
                  type="number"
                  min={1}
                  placeholder="Full dataset"
                  value={draft.limit}
                  disabled={running}
                  onChange={(e) => setField("limit", e.target.value)}
                />
              </Field>
              <Field label="Concurrency">
                <Input
                  className="w-full"
                  type="number"
                  min={1}
                  value={draft.max_concurrency}
                  disabled={running}
                  onChange={(e) => setField("max_concurrency", e.target.value)}
                />
              </Field>
            </div>
          </FieldGroup>
          <FieldGroup
            label="Reviewer model"
            description="Model and reasoning effort passed to the reviewer graph for each PR."
          >
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Model">
                <Select
                  value={draft.model_id}
                  onValueChange={(value) => {
                    const model = options.data?.models.find((m) => m.id === value)
                    if (!model) return
                    setDraft((current) => ({
                      ...current,
                      model_id: model.id,
                      reasoning_effort: model.efforts.includes(current.reasoning_effort)
                        ? current.reasoning_effort
                        : model.default_effort,
                    }))
                  }}
                  disabled={running || options.isLoading}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {options.data?.models.map((model) => (
                      <SelectItem key={model.id} value={model.id}>
                        {model.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field label="Effort">
                <Select
                  value={draft.reasoning_effort}
                  onValueChange={(value) => {
                    if (value) setField("reasoning_effort", value)
                  }}
                  disabled={running || !currentModel}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="effort" />
                  </SelectTrigger>
                  <SelectContent>
                    {currentModel?.efforts.map((effort) => (
                      <SelectItem key={effort} value={effort}>
                        {effort}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            </div>
          </FieldGroup>
          <FieldGroup
            label="Scoring"
            description="Choose whether to score all findings or only findings that would be surfaced in production."
          >
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <Field label="Score mode">
                <Select
                  value={draft.score_mode}
                  onValueChange={(value) => {
                    if (value === "all_findings" || value === "surfaced_findings") {
                      setField("score_mode", value)
                    }
                  }}
                  disabled={running}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all_findings">All findings</SelectItem>
                    <SelectItem value="surfaced_findings">
                      Surfaced findings
                    </SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field label="Severity threshold">
                <Select
                  value={draft.severity_threshold}
                  onValueChange={(value) => {
                    if (
                      value === "low" ||
                      value === "medium" ||
                      value === "high" ||
                      value === "critical"
                    ) {
                      setField("severity_threshold", value)
                    }
                  }}
                  disabled={running || draft.score_mode !== "surfaced_findings"}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="low">low</SelectItem>
                    <SelectItem value="medium">medium</SelectItem>
                    <SelectItem value="high">high</SelectItem>
                    <SelectItem value="critical">critical</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field label="Cap">
                <Input
                  className="w-full"
                  type="number"
                  min={0}
                  value={draft.cap}
                  disabled={running || draft.score_mode !== "surfaced_findings"}
                  onChange={(e) => setField("cap", e.target.value)}
                />
              </Field>
            </div>
          </FieldGroup>
          <FieldGroup
            label="Advanced"
            description="Override the LangSmith project, LangGraph URL, or reviewer assistant id for this run."
          >
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <Field label="LangSmith project">
                <Input
                  className="w-full"
                  placeholder="open-swe-evals"
                  value={draft.langsmith_project}
                  disabled={running}
                  onChange={(e) => setField("langsmith_project", e.target.value)}
                />
              </Field>
              <Field label="LangGraph URL">
                <Input
                  className="w-full"
                  placeholder="Optional"
                  value={draft.langgraph_url}
                  disabled={running}
                  onChange={(e) => setField("langgraph_url", e.target.value)}
                />
              </Field>
              <Field label="Assistant ID">
                <Input
                  className="w-full"
                  placeholder="reviewer"
                  value={draft.assistant_id}
                  disabled={running}
                  onChange={(e) => setField("assistant_id", e.target.value)}
                />
              </Field>
            </div>
          </FieldGroup>
          <div className="flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
            <div className="flex flex-col gap-0.5">
              <span className="text-xs font-medium text-foreground">Start</span>
              <span className="text-xs text-muted-foreground">
                Only one reviewer eval can run at a time.
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                onClick={() => start.mutate()}
                disabled={running || start.isPending || options.isLoading}
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
          </div>
        </div>
        {error && <p className="px-4 pb-3 text-xs text-destructive">{error}</p>}
      </SettingsSection>

      <SettingsSection
        title="Current run"
        description="Status and resolved configuration for the latest reviewer eval run."
      >
        <ReviewerEvalStatusView data={data ?? null} />
      </SettingsSection>
    </>
  )
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
      <StatusLine label="Run name" value={data.run_name ?? config?.experiment_prefix} />
      <StatusLine label="Dataset" value={config?.dataset_name} />
      <StatusLine label="Limit" value={data.limit ? String(data.limit) : "full dataset"} />
      <StatusLine label="Model" value={config?.model_id} />
      <StatusLine label="Effort" value={config?.reasoning_effort} />
      <StatusLine label="Score mode" value={config?.score_mode} />
      <StatusLine label="Threshold" value={config?.severity_threshold} />
      <StatusLine label="Cap" value={config ? String(config.cap) : null} />
      <StatusLine label="LangSmith project" value={data.langsmith_project} />
      <StatusLine label="PID" value={data.pid ? String(data.pid) : null} />
      <StatusLine label="Exit code" value={data.exit_code !== null ? String(data.exit_code) : null} />
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
  const status = useQuery({
    queryKey: ["reviewerEval"],
    queryFn: api.getReviewerEval,
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 5000 : false,
  })
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
      description="Live tail of the eval process output. Each PR logs start and finish lines while the run is active."
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
            {running ? "Waiting for output…" : "No output yet. Run an eval to see logs here."}
          </p>
        )}
      </div>
    </SettingsSection>
  )
}
