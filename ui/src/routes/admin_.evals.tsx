import { createFileRoute } from "@tanstack/react-router"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"
import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import {
  FormField,
  FormSection,
  FormStack,
} from "@langchain/gtm-platform-design-system/patterns/form-field"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import {
  StatReadout,
  type StatTone,
} from "@langchain/gtm-platform-design-system/patterns/stat-readout"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import {
  Button,
  buttonVariants,
} from "@langchain/gtm-platform-design-system/ui/button"
import { Checkbox } from "@langchain/gtm-platform-design-system/ui/checkbox"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import { ScrollArea } from "@langchain/gtm-platform-design-system/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@langchain/gtm-platform-design-system/ui/select"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"

import type {
  ReviewerEvalConfig,
  ReviewerEvalStartRequest,
  ReviewerEvalStatus,
} from "@/lib/api"
import { SettingsPage } from "@/components/AppShell"
import {
  AlertTriangle,
  Copy,
  ExternalLink,
  Terminal,
} from "@/components/glyphs"
import { ModelPairControl } from "@/features/settings/components/WorkspaceSettingsSections"
import { api } from "@/lib/api"
import { pageTitle } from "@/lib/pageTitle"
import { useOptions } from "@/lib/profile"

export const Route = createFileRoute("/admin_/evals")({
  component: ReviewerEvalPage,
  head: () => ({ meta: [{ title: pageTitle("Reviewer evals") }] }),
})

const STATUS_TONE: Record<ReviewerEvalStatus["status"], StatTone> = {
  idle: "neutral",
  starting: "info",
  running: "info",
  completed: "positive",
  failed: "risk",
}

function ReviewerEvalPage() {
  return (
    <SettingsPage
      adminOnly
      title="Reviewer eval"
      description="Runs the reviewer benchmark in a LangSmith sandbox against this deployment."
    >
      <ReviewerEvalRunConfigSection />
      <ReviewerEvalStatusSection />
      <ReviewerEvalLogs />
    </SettingsPage>
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
    <PageSection
      title="Run config"
      description="Defaults to the previous run's config. Each run boots a sandbox at the deployed commit."
      contained
      inset="padded"
    >
      <FormStack>
        <FormSection title="Benchmark">
          <FormField
            label="Dataset"
            control={
              <Input
                value={value?.dataset_name ?? ""}
                onChange={(e) => update({ dataset_name: e.target.value })}
              />
            }
          />
          <FormField
            label="Run name"
            help="LangSmith experiment prefix."
            control={
              <Input
                value={value?.experiment_prefix ?? ""}
                onChange={(e) => update({ experiment_prefix: e.target.value })}
              />
            }
          />
          <FormField
            label="Limit"
            help="Run only the first N PRs. Blank runs the full dataset."
            control={
              <Input
                className="w-24"
                type="number"
                min={1}
                placeholder="all"
                value={value?.limit ?? ""}
                onChange={(e) =>
                  update({
                    limit: e.target.value ? Number(e.target.value) : null,
                  })
                }
              />
            }
          />
        </FormSection>
        <FormSection title="Reviewer">
          <FormField
            label="Reviewer model"
            control={
              <Inline role="group" aria-label="Reviewer model">
                <ModelPairControl
                  models={models}
                  model={value?.model_id ?? null}
                  effort={value?.reasoning_effort ?? null}
                  onChange={(model_id, reasoning_effort) =>
                    update({ model_id, reasoning_effort })
                  }
                  disabled={!value}
                />
              </Inline>
            }
          />
          <FormField
            label="Max concurrency"
            help="PRs reviewed at once on the deployment."
            control={
              <Input
                className="w-24"
                type="number"
                min={1}
                value={value?.max_concurrency ?? ""}
                onChange={(e) =>
                  update({ max_concurrency: Number(e.target.value) })
                }
              />
            }
          />
          <FormField
            label="Score mode"
            control={
              <ChoiceSelect
                value={value?.score_mode}
                options={["surfaced_findings", "all_findings"] as const}
                onChange={(score_mode) => update({ score_mode })}
              />
            }
          />
          <FormField
            label="Severity threshold"
            control={
              <ChoiceSelect
                value={value?.severity_threshold}
                options={["low", "medium", "high", "critical"] as const}
                onChange={(severity_threshold) =>
                  update({ severity_threshold })
                }
              />
            }
          />
        </FormSection>
      </FormStack>
      <Inline justify="end">
        <Button
          type="button"
          disabled={disabled}
          onClick={() => value && start.mutate(value)}
        >
          {start.isPending ? "Starting…" : "Start eval"}
        </Button>
      </Inline>
    </PageSection>
  )
}

/** A closed-vocabulary select. FormField hands it the id and aria wiring for its trigger. */
function ChoiceSelect<T extends string>({
  value,
  options,
  onChange,
  id,
  "aria-describedby": describedBy,
  "aria-invalid": invalid,
}: {
  value: T | undefined
  options: ReadonlyArray<T>
  onChange: (value: T) => void
  id?: string
  "aria-describedby"?: string
  "aria-invalid"?: boolean
}) {
  return (
    <Select
      value={value ?? null}
      onValueChange={(next) => next && onChange(next)}
    >
      <SelectTrigger
        id={id}
        aria-describedby={describedBy}
        aria-invalid={invalid}
        className="w-40"
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option} value={option}>
            {option}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function ReviewerEvalStatusSection() {
  const status = useReviewerEvalStatus()
  const data = status.data ?? null
  return (
    <PageSection
      title="Current run"
      description="Status and resolved configuration for the latest reviewer eval run."
      actions={
        data?.experiment_url ? (
          <a
            href={data.experiment_url}
            target="_blank"
            rel="noreferrer"
            className={buttonVariants({ variant: "outline", size: "compact" })}
          >
            <Icon icon={ExternalLink} size="sm" />
            View experiment in LangSmith
          </a>
        ) : undefined
      }
    >
      <ReviewerEvalStatusView data={data} />
    </PageSection>
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
      <Stack gap="sm" role="status" aria-label="Loading reviewer eval status">
        <Skeleton className="h-row-data w-full" />
        <Skeleton className="h-row-data w-full" />
      </Stack>
    )
  }

  const config = data.config_snapshot
  return (
    <Stack gap="lg">
      {data.error ? (
        <StateNotice
          tone="RISK"
          icon={AlertTriangle}
          title="The reviewer eval stopped with an error"
          description={data.error}
        />
      ) : null}
      <Box className="grid gap-x-6 sm:grid-cols-2">
        <StatReadout
          shape="field"
          label="Status"
          value={data.status}
          tone={STATUS_TONE[data.status]}
        />
        <StatReadout
          shape="field"
          label="Progress"
          value={progressLabel(data)}
        />
        <StatReadout
          shape="field"
          label="Run name"
          value={data.run_name ?? config?.experiment_prefix}
        />
        <StatReadout
          shape="field"
          label="Dataset"
          value={config?.dataset_name}
        />
        <StatReadout
          shape="field"
          label="Limit"
          value={data.limit ? String(data.limit) : "full dataset"}
        />
        <StatReadout shape="field" label="Model" value={config?.model_id} />
        <StatReadout
          shape="field"
          label="Effort"
          value={config?.reasoning_effort}
        />
        <StatReadout
          shape="field"
          label="Score mode"
          value={config?.score_mode}
        />
        <StatReadout
          shape="field"
          label="Threshold"
          value={config?.severity_threshold}
        />
        <StatReadout
          shape="field"
          label="LangSmith project"
          value={data.langsmith_project}
        />
        <StatReadout
          shape="field"
          label="Triggered by"
          value={data.created_by}
        />
        <StatReadout shape="field" label="Sandbox" value={data.worker_id} />
        {data.started_at && (
          <StatReadout
            shape="field"
            label="Started"
            value={new Date(data.started_at).toLocaleString()}
          />
        )}
        {data.finished_at && (
          <StatReadout
            shape="field"
            label="Finished"
            value={new Date(data.finished_at).toLocaleString()}
          />
        )}
      </Box>
    </Stack>
  )
}

/** Within this distance of the bottom, the log counts as followed. */
const FOLLOW_THRESHOLD_PX = 24

function ReviewerEvalLogs() {
  const status = useReviewerEvalStatus()
  const logTail = status.data?.log_tail ?? null
  const running = isActive(status.data)

  // State re-runs the listener effect when the viewport mounts; the ref is
  // what gets scrolled, since React state values must not be mutated.
  const viewportRef = useRef<HTMLDivElement | null>(null)
  const [viewport, setViewport] = useState<HTMLDivElement | null>(null)
  const attachViewport = useCallback((node: HTMLDivElement | null) => {
    viewportRef.current = node
    setViewport(node)
  }, [])
  const [follow, setFollow] = useState(true)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    const node = viewportRef.current
    if (follow && node) {
      node.scrollTop = node.scrollHeight
    }
  }, [logTail, follow, viewport])

  useEffect(() => {
    if (!viewport) return
    const onScroll = () => {
      setFollow(
        viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight <
          FOLLOW_THRESHOLD_PX
      )
    }
    viewport.addEventListener("scroll", onScroll)
    return () => viewport.removeEventListener("scroll", onScroll)
  }, [viewport])

  const copyLogs = async () => {
    if (!logTail) return
    await navigator.clipboard.writeText(logTail)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }

  return (
    <PageSection
      title="Output"
      description="Live tail of the eval output. Each PR logs start and finish lines while the run is active."
      contained
      actions={
        <Inline gap="md" align="center">
          <Inline
            render={<label />}
            gap="sm"
            align="center"
            className="text-label text-ink-subtle"
          >
            <Checkbox
              checked={follow}
              onCheckedChange={(checked) => setFollow(checked)}
            />
            Follow
          </Inline>
          <Button
            size="compact"
            variant="outline"
            onClick={() => void copyLogs()}
            disabled={!logTail}
          >
            <Icon icon={Copy} size="sm" />
            {copied ? "Copied" : "Copy logs"}
          </Button>
        </Inline>
      }
    >
      {logTail ? (
        <ScrollArea
          overflow="vertical"
          viewportRef={attachViewport}
          viewportClassName="max-h-112"
        >
          <Box
            render={<pre />}
            padding="lg"
            className="font-mono text-label break-words whitespace-pre-wrap text-ink"
          >
            {logTail}
          </Box>
        </ScrollArea>
      ) : running ? (
        <EmptyState icon={Terminal} title="Waiting for output" />
      ) : (
        <EmptyState
          icon={Terminal}
          title="No output yet"
          description="Launch a reviewer eval to see logs here."
        />
      )}
    </PageSection>
  )
}
