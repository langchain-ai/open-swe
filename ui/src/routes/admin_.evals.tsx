import { Navigate, createFileRoute } from "@tanstack/react-router"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"
import { Button } from "@langchain/macaw-components/Button"
import { Link as MacawLink } from "@langchain/macaw-components/Link"
import { Checkbox } from "@langchain/macaw-components/Checkbox"
import { Input } from "@langchain/macaw-components/Input"
import { Select } from "@langchain/macaw-components/Select"
import { Skeleton } from "@langchain/macaw-components/Skeleton"

import type {
  ReviewerEvalConfig,
  ReviewerEvalStartRequest,
  ReviewerEvalStatus,
} from "@/lib/api"
import { AppShell, SettingsRow, SettingsSection } from "@/components/AppShell"
import { ModelPairControl } from "@/features/settings/components/WorkspaceSettingsSections"
import { api } from "@/lib/api"
import { pageTitle } from "@/lib/pageTitle"
import { RequireLogin } from "@/lib/auth-redirect"
import { useOptions } from "@/lib/profile"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/admin_/evals")({
  component: ReviewerEvalPage,
  head: () => ({ meta: [{ title: pageTitle("Reviewer evals") }] }),
})

function ReviewerEvalPage() {
  const session = useSession()

  if (session.isLoading) {
    return (
      <main className="p-space-6">
        <Skeleton className="h-64 w-full" />
      </main>
    )
  }
  if (!session.data) return <RequireLogin />
  if (!session.data.is_admin) return <Navigate to="/my-settings" />

  return (
    <AppShell
      user={session.data}
      title="Reviewer Eval"
      description="Runs the reviewer benchmark in a LangSmith sandbox against this deployment. Progress streams here live."
    >
      <ReviewerEvalRunConfigSection />
      <ReviewerEvalStatusSection />
      <ReviewerEvalLogs />
    </AppShell>
  )
}

function useReviewerEvalStatus() {
  return useQuery({
    queryKey: ["reviewerEval"],
    queryFn: api.getReviewerEval,
    refetchInterval: (query) => (isActive(query.state.data) ? 5000 : false),
  })
}

function isActive(data: ReviewerEvalStatus | undefined): boolean {
  return data?.status === "running" || data?.status === "starting"
}

function startRequest(
  config: ReviewerEvalConfig,
  limit: number | null
): ReviewerEvalStartRequest {
  return {
    dataset_name: config.dataset_name,
    experiment_prefix: config.experiment_prefix,
    max_concurrency: config.max_concurrency,
    model_id: config.model_id,
    reasoning_effort: config.reasoning_effort,
    score_mode: config.score_mode,
    severity_threshold: config.severity_threshold,
    limit,
  }
}

function ReviewerEvalRunConfigSection() {
  const status = useReviewerEvalStatus()
  const models = useOptions().data?.models ?? []
  const queryClient = useQueryClient()
  const [form, setForm] = useState<ReviewerEvalStartRequest | null>(null)
  const snapshot = status.data?.config_snapshot
  const value =
    form ??
    (snapshot ? startRequest(snapshot, status.data?.limit ?? null) : null)
  const start = useMutation({
    mutationFn: api.startReviewerEval,
    onSuccess: (data) => queryClient.setQueryData(["reviewerEval"], data),
    onError: (error) =>
      toast.error("Couldn’t start the reviewer eval", {
        description: error.message,
      }),
  })
  const disabled = !value || isActive(status.data) || start.isPending
  const update = (patch: Partial<ReviewerEvalStartRequest>) =>
    value && setForm({ ...value, ...patch })

  return (
    <SettingsSection
      title="Run config"
      description="Defaults to the previous run's config. Each run boots a sandbox at the deployed commit."
      action={
        <Button
          size="xs"
          color="primary"
          disabled={disabled}
          onClick={() => value && start.mutate(value)}
        >
          {start.isPending ? "Starting…" : "Start eval"}
        </Button>
      }
    >
      <SettingsRow
        label="Dataset"
        control={
          <Input
            aria-label="Dataset"
            size="md"
            className="w-64"
            value={value?.dataset_name ?? ""}
            onChange={(dataset_name) => update({ dataset_name })}
          />
        }
      />
      <SettingsRow
        label="Run name"
        description="LangSmith experiment prefix."
        control={
          <Input
            aria-label="Run name"
            size="md"
            className="w-64"
            value={value?.experiment_prefix ?? ""}
            onChange={(experiment_prefix) => update({ experiment_prefix })}
          />
        }
      />
      <SettingsRow
        label="Reviewer model"
        control={
          <ModelPairControl
            label="Reviewer"
            models={models}
            model={value?.model_id ?? null}
            effort={value?.reasoning_effort ?? null}
            onChange={(model_id, reasoning_effort) =>
              update({ model_id, reasoning_effort })
            }
            disabled={!value}
          />
        }
      />
      <SettingsRow
        label="Max concurrency"
        description="PRs reviewed at once on the deployment."
        control={
          <Input
            aria-label="Max concurrency"
            size="md"
            className="w-24"
            type="number"
            min={1}
            value={value ? String(value.max_concurrency) : ""}
            onChange={(next) => update({ max_concurrency: Number(next) })}
          />
        }
      />
      <SettingsRow
        label="Limit"
        description="Run only the first N PRs. Blank runs the full dataset."
        control={
          <Input
            aria-label="Limit"
            size="md"
            className="w-24"
            type="number"
            min={1}
            placeholder="all"
            value={value?.limit != null ? String(value.limit) : ""}
            onChange={(next) => update({ limit: next ? Number(next) : null })}
          />
        }
      />
      <SettingsRow
        label="Score mode"
        control={
          <ChoiceSelect
            label="Score mode"
            value={value?.score_mode}
            options={["surfaced_findings", "all_findings"] as const}
            onChange={(score_mode) => update({ score_mode })}
          />
        }
      />
      <SettingsRow
        label="Severity threshold"
        control={
          <ChoiceSelect
            label="Severity threshold"
            value={value?.severity_threshold}
            options={["low", "medium", "high", "critical"] as const}
            onChange={(severity_threshold) => update({ severity_threshold })}
          />
        }
      />
    </SettingsSection>
  )
}

function ChoiceSelect<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T | undefined
  options: ReadonlyArray<T>
  onChange: (value: T) => void
}) {
  return (
    <Select
      aria-label={label}
      options={options.map((option) => ({ value: option, label: option }))}
      value={value}
      onChange={(next) => next && onChange(next)}
      triggerClassName="w-40"
    />
  )
}

function ReviewerEvalStatusSection() {
  const status = useReviewerEvalStatus()
  return (
    <SettingsSection
      title="Current Run"
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
      <div className="p-space-4 text-xs text-secondary">
        Loading reviewer eval status…
      </div>
    )
  }

  const config = data.config_snapshot
  return (
    <div className="grid gap-space-2 p-space-4 text-xs text-secondary sm:grid-cols-2">
      <StatusLine label="Status" value={data.status} strong />
      <StatusLine label="Progress" value={progressLabel(data)} />
      <StatusLine
        label="Run name"
        value={data.run_name ?? config?.experiment_prefix}
      />
      <StatusLine label="Dataset" value={config?.dataset_name} />
      <StatusLine
        label="Limit"
        value={data.limit ? String(data.limit) : "full dataset"}
      />
      <StatusLine label="Model" value={config?.model_id} />
      <StatusLine label="Effort" value={config?.reasoning_effort} />
      <StatusLine label="Score mode" value={config?.score_mode} />
      <StatusLine label="Threshold" value={config?.severity_threshold} />
      <StatusLine label="LangSmith project" value={data.langsmith_project} />
      <StatusLine label="Triggered by" value={data.created_by} />
      <StatusLine label="Sandbox" value={data.worker_id} />
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
        <MacawLink
          href={data.experiment_url}
          target="_blank"
          rel="noreferrer"
          variant="sm"
        >
          View experiment in LangSmith
        </MacawLink>
      )}
      {data.error && <span className="text-error-secondary">{data.error}</span>}
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
      <span className={strong ? "font-medium text-primary" : ""}>
        {value || "—"}
      </span>
    </span>
  )
}

function ReviewerEvalLogs() {
  const status = useReviewerEvalStatus()
  const logTail = status.data?.log_tail ?? null
  const running = isActive(status.data)

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
        <div className="flex items-center gap-space-2">
          <Button
            size="xs"
            color="secondary"
            variant="outlined"
            onClick={() => void copyLogs()}
            disabled={!logTail}
          >
            {copied ? "Copied" : "Copy logs"}
          </Button>
          <Checkbox
            label="Follow"
            labelClassName="text-xs text-secondary"
            checked={follow}
            onCheckedChange={(checked) => setFollow(checked === true)}
          />
        </div>
      }
    >
      <div className="p-space-4">
        {logTail ? (
          <pre
            ref={scrollRef}
            onScroll={(e) => {
              const el = e.currentTarget
              const atBottom =
                el.scrollHeight - el.scrollTop - el.clientHeight < 24
              setFollow(atBottom)
            }}
            className="max-h-[28rem] overflow-auto rounded-md bg-surface-level-2 p-space-3 font-mono text-xs break-words whitespace-pre-wrap text-primary"
          >
            {logTail}
          </pre>
        ) : (
          <p className="text-xs text-secondary">
            {running
              ? "Waiting for output…"
              : "No output yet. Launch a reviewer eval to see logs here."}
          </p>
        )}
      </div>
    </SettingsSection>
  )
}
