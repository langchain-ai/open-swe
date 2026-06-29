import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"

import type {
  LinearCatalogResult,
  TicketIntakeConfig,
  TicketIntakePreview,
  TicketIntakeUpdateBody,
} from "@/lib/api"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"

function splitList(value: string): Array<string> {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
}

function joinList(value: Array<string> | undefined): string {
  return (value ?? []).join(", ")
}

function draftFromConfig(
  config: TicketIntakeConfig | undefined
): TicketIntakeUpdateBody {
  return {
    provider: "linear",
    team_ids: config?.tracker_config.team_ids ?? [],
    team_keys: config?.tracker_config.team_keys ?? [],
    team_names: config?.tracker_config.team_names ?? [],
    linear_project_ids: config?.tracker_config.linear_project_ids ?? [],
    linear_project_names: config?.tracker_config.linear_project_names ?? [],
    labels: config?.queue_eligibility_policy.labels ?? ["agent-ready"],
    ready_states: config?.queue_eligibility_policy.ready_states ?? ["ready"],
    excluded_statuses: config?.queue_eligibility_policy.excluded_statuses ?? [
      "done",
      "completed",
      "canceled",
      "cancelled",
      "duplicate",
    ],
    required_fields: config?.queue_eligibility_policy.required_fields ?? [
      "description",
    ],
    missing_readiness:
      config?.queue_eligibility_policy.missing_readiness === "skip"
        ? "skip"
        : "not-ready",
    polling_interval_minutes:
      config?.queue_eligibility_policy.polling_interval_minutes ?? 5,
  }
}

function CatalogSummary({ result }: { result: LinearCatalogResult | null }) {
  if (!result) return null
  if (result.status !== "connected") {
    return (
      <p className="px-4 pb-3 text-xs text-destructive">
        {result.error ?? "Linear connection failed."}
      </p>
    )
  }
  return (
    <div className="grid gap-3 px-4 pb-3 text-xs text-muted-foreground sm:grid-cols-2">
      <div>
        <div className="font-medium text-foreground">Teams</div>
        <div className="mt-1">
          {result.teams.length
            ? result.teams
                .slice(0, 5)
                .map((team) => team.key ?? team.name ?? team.id)
                .join(", ")
            : "None returned"}
        </div>
      </div>
      <div>
        <div className="font-medium text-foreground">Projects</div>
        <div className="mt-1">
          {result.projects.length
            ? result.projects
                .slice(0, 5)
                .map((project) => project.name ?? project.id)
                .join(", ")
            : "None returned"}
        </div>
      </div>
    </div>
  )
}

function PreviewSummary({ result }: { result: TicketIntakePreview | null }) {
  if (!result) return null
  if (result.status !== "previewed") {
    return (
      <p className="px-4 pb-3 text-xs text-destructive">
        {result.error ?? "Preview failed."}
      </p>
    )
  }
  return (
    <div className="space-y-3 px-4 pb-3">
      <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        {Object.entries(result.counts).map(([key, value]) => (
          <div key={key} className="rounded-md border border-border p-2">
            <div className="text-[10px] font-medium text-muted-foreground uppercase">
              {key}
            </div>
            <div className="mt-0.5 text-sm font-medium">{value}</div>
          </div>
        ))}
      </div>
      <div className="divide-y divide-border rounded-md border border-border">
        {result.items.slice(0, 8).map((item, index) => (
          <div
            key={`${item.identifier ?? index}-${item.action}`}
            className="grid gap-2 px-3 py-2 text-xs sm:grid-cols-[90px_110px_1fr]"
          >
            <Badge variant={item.action === "queued" ? "default" : "secondary"}>
              {item.action}
            </Badge>
            <span className="text-muted-foreground">
              {item.identifier ?? item.reason ?? "-"}
            </span>
            <span className="min-w-0 truncate">{item.title ?? "-"}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export function WorkspaceTicketIntakeSection({
  projectId,
}: {
  projectId: string
}) {
  const qc = useQueryClient()
  const config = useQuery({
    queryKey: ["ticketIntake", projectId],
    queryFn: () => api.getTicketIntake(projectId),
  })
  const [draft, setDraft] = useState<TicketIntakeUpdateBody>(() =>
    draftFromConfig(undefined)
  )
  const draftRef = useRef(draft)
  const [catalog, setCatalog] = useState<LinearCatalogResult | null>(null)
  const [preview, setPreview] = useState<TicketIntakePreview | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (config.data) {
      const nextDraft = draftFromConfig(config.data)
      draftRef.current = nextDraft
      setDraft(nextDraft)
    }
  }, [config.data])

  const updateDraft = (
    updater: (current: TicketIntakeUpdateBody) => TicketIntakeUpdateBody
  ) => {
    setDraft((current) => {
      const nextDraft = updater(current)
      draftRef.current = nextDraft
      return nextDraft
    })
  }

  const save = useMutation({
    mutationFn: () => api.saveTicketIntake(projectId, draftRef.current),
    onSuccess: (next) => {
      setError(null)
      setCatalog(null)
      setPreview(null)
      const nextDraft = draftFromConfig(next)
      draftRef.current = nextDraft
      setDraft(nextDraft)
      void qc.invalidateQueries({ queryKey: ["ticketIntake", projectId] })
      void qc.invalidateQueries({ queryKey: ["deliveryProjects"] })
      void qc.invalidateQueries({
        queryKey: ["deliveryProjectReadiness", projectId],
      })
    },
    onError: (e: Error) => setError(e.message),
  })
  const testConnection = useMutation({
    mutationFn: () => api.testTicketIntakeConnection(projectId),
    onSuccess: (result) => {
      setError(null)
      setCatalog(result)
    },
    onError: (e: Error) => setError(e.message),
  })
  const previewIntake = useMutation({
    mutationFn: () => api.previewTicketIntake(projectId),
    onSuccess: (result) => {
      setError(null)
      setPreview(result)
    },
    onError: (e: Error) => setError(e.message),
  })

  const credential = config.data?.credential

  return (
    <SettingsSection
      title="Ticket Intake"
      description="Configure the Linear V1 intake that the five-minute poller reads. Test and preview are read-only and do not start delivery work."
    >
      <div id="ticket-intake" className="divide-y divide-border">
        <SettingsRow
          label="Provider"
          description="V1 supports Linear intake."
          control={<Badge variant="default">Linear</Badge>}
        />
        <SettingsRow
          label="Linear credential"
          description={
            credential?.available
              ? `${credential.source ?? "Linear credential"} is configured.`
              : "Connect a Linear provider token in Profile Settings. Polling and preview cannot read Linear yet."
          }
          control={
            <Badge variant={credential?.available ? "default" : "destructive"}>
              {credential?.available ? "Available" : "Missing"}
            </Badge>
          }
        />
        <SettingsRow
          label="Team keys"
          description="Comma-separated Linear team keys."
          control={
            <Input
              aria-label="Linear team keys"
              className="w-full sm:w-80"
              value={joinList(draft.team_keys)}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  team_keys: splitList(e.target.value),
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Project IDs"
          description="Comma-separated Linear project IDs."
          control={
            <Input
              aria-label="Linear project IDs"
              className="w-full sm:w-80"
              value={joinList(draft.linear_project_ids)}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  linear_project_ids: splitList(e.target.value),
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Project names"
          description="Optional comma-separated Linear project names."
          control={
            <Input
              aria-label="Linear project names"
              className="w-full sm:w-80"
              value={joinList(draft.linear_project_names)}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  linear_project_names: splitList(e.target.value),
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Ready labels"
          description="Issues with the first configured label are eligible for queued delivery."
          control={
            <Input
              aria-label="Ready labels"
              className="w-full sm:w-80"
              value={joinList(draft.labels)}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  labels: splitList(e.target.value),
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Excluded states"
          description="State names/types ignored by polling and preview."
          control={
            <Input
              aria-label="Excluded states"
              className="w-full sm:w-80"
              value={joinList(draft.excluded_statuses)}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  excluded_statuses: splitList(e.target.value),
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Required fields"
          description="Missing fields create blocked queue previews/items."
          control={
            <Input
              aria-label="Required fields"
              className="w-full sm:w-80"
              value={joinList(draft.required_fields)}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  required_fields: splitList(e.target.value),
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Missing readiness"
          description="Without the ready label, V1 can mark not-ready or ignore."
          control={
            <select
              aria-label="Missing readiness behavior"
              className="h-7 w-full rounded-md border border-input bg-input/20 px-2 text-xs sm:w-48"
              value={draft.missing_readiness}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  missing_readiness:
                    e.target.value === "skip" ? "skip" : "not-ready",
                }))
              }
            >
              <option value="not-ready">Mark not-ready</option>
              <option value="skip">Ignore</option>
            </select>
          }
        />
        <SettingsRow
          label="Polling"
          description="The V1 poller runs every five minutes and only updates queue records."
          control={
            <span className="text-xs text-muted-foreground">
              Every {draft.polling_interval_minutes} minutes
            </span>
          }
        />
        <SettingsRow
          label="Actions"
          description="Test connection lists Linear teams/projects. Preview classifies issues without queueing or starting work."
          control={
            <div className="flex flex-wrap justify-end gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => testConnection.mutate()}
                disabled={testConnection.isPending || config.isLoading}
              >
                Test connection
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => previewIntake.mutate()}
                disabled={previewIntake.isPending || config.isLoading}
              >
                Preview intake
              </Button>
              <Button
                size="sm"
                onClick={() => save.mutate()}
                disabled={save.isPending || config.isLoading}
              >
                Save
              </Button>
            </div>
          }
        />
      </div>
      <CatalogSummary result={catalog} />
      <PreviewSummary result={preview} />
      {error && <p className="px-4 pb-3 text-xs text-destructive">{error}</p>}
    </SettingsSection>
  )
}
