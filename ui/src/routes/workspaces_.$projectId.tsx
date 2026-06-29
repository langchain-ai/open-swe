import { Link, Navigate, createFileRoute } from "@tanstack/react-router"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import type {
  DeliveryAutoModeTickResult,
  DeliveryProjectReadiness,
  DeliveryProjectSummary,
  DeliveryRunRollup,
} from "@/lib/api"
import { AppShell, SettingsSection } from "@/components/AppShell"
import { WorkspaceCredentialsSection } from "@/components/WorkspaceCredentialsSection"
import { WorkspaceDeliveryPolicySection } from "@/components/WorkspaceDeliveryPolicySection"
import { WorkspaceModelEndpointsSection } from "@/components/WorkspaceModelEndpointsSection"
import { WorkspaceModelRoutingSection } from "@/components/WorkspaceModelRoutingSection"
import { WorkspaceRepositoriesSection } from "@/components/WorkspaceRepositoriesSection"
import { WorkspaceTicketIntakeSection } from "@/components/WorkspaceTicketIntakeSection"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/workspaces_/$projectId")({
  component: WorkspaceDetailPage,
})

function text(value: unknown): string {
  return typeof value === "string" && value.trim() ? value.trim() : "Not set"
}

function valueText(value: unknown): string {
  if (Array.isArray(value)) {
    const values = value
      .filter((item): item is string => typeof item === "string")
      .map((item) => item.trim())
      .filter(Boolean)
    return values.length ? values.join(", ") : "Not set"
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value)
  }
  return text(value)
}

function entries(value: Record<string, unknown>): Array<[string, string]> {
  return Object.entries(value)
    .filter(([, item]) => item !== undefined && item !== null && item !== "")
    .map(([key, item]) => [key, valueText(item)])
}

function StatusBadge({ project }: { project: DeliveryProjectSummary }) {
  if (project.kill_switch) return <Badge variant="destructive">Stopped</Badge>
  if (!project.active) return <Badge variant="secondary">Inactive</Badge>
  return <Badge variant="default">Active</Badge>
}

function autoModeState(project: DeliveryProjectSummary): string {
  if (project.kill_switch) return "Stopped"
  if (!project.active) return "Inactive"
  return "Ready"
}

function KeyValueGrid({
  items,
}: {
  items: Array<[string, string | number | boolean]>
}) {
  if (!items.length) {
    return (
      <div className="px-4 py-5 text-xs text-muted-foreground">
        No values configured.
      </div>
    )
  }
  return (
    <dl className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
      {items.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-[10px] font-medium text-muted-foreground uppercase">
            {label.replaceAll("_", " ")}
          </dt>
          <dd className="mt-0.5 truncate text-xs text-foreground">
            {String(value)}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function TokenList({ items }: { items: Array<string> }) {
  if (!items.length) {
    return (
      <span className="text-xs text-muted-foreground">None configured</span>
    )
  }
  return (
    <div className="flex flex-wrap gap-1.5 p-4">
      {items.map((item) => (
        <span
          key={item}
          className="rounded-sm border border-border bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
        >
          {item}
        </span>
      ))}
    </div>
  )
}

function shortSha(value: string | null | undefined): string | null {
  return value ? value.slice(0, 8) : null
}

function objectLabel(value: unknown): string {
  if (typeof value === "string" && value.trim()) return value.trim()
  if (!value || typeof value !== "object") return ""
  const record = value as Record<string, unknown>
  const name = text(record.name)
  const status = text(record.status)
  const message = text(record.message)
  const code = text(record.code)
  if (name !== "Not set" && status !== "Not set") return `${name}: ${status}`
  if (code !== "Not set" && message !== "Not set") return `${code}: ${message}`
  if (message !== "Not set") return message
  if (status !== "Not set") return status
  return JSON.stringify(value)
}

function EvidenceLink({ value }: { value: unknown }) {
  const label = objectLabel(value)
  if (!label) return null
  if (label.startsWith("http://") || label.startsWith("https://")) {
    return (
      <a
        href={label}
        className="truncate text-muted-foreground hover:text-foreground"
        target="_blank"
        rel="noreferrer"
      >
        {label}
      </a>
    )
  }
  return <span className="truncate text-muted-foreground">{label}</span>
}

function DeliveryRunDetails({ delivery }: { delivery?: DeliveryRunRollup | null }) {
  if (!delivery) return null
  const smokeAcceptance = Object.entries(delivery.smokeProof?.acceptance ?? {})
  const threadItems = [
    ["Worker", delivery.workerThreadId],
    ["Review", delivery.reviewerThreadId],
    ["QA", delivery.qaThreadId],
    ["Merge", delivery.mergeWorkerThreadId],
  ].filter(([, value]) => value)
  const statusItems = [
    ["Branch", delivery.branch],
    ["Reviewed", shortSha(delivery.reviewedSha)],
    ["Merge", delivery.mergeStatus],
  ].filter(([, value]) => value)
  const blockers = delivery.blockers.map(objectLabel).filter(Boolean)
  return (
    <div className="space-y-2 sm:col-span-3">
      <div className="flex flex-wrap gap-1.5 text-[10px] text-muted-foreground">
        {delivery.gateRollup ? (
          <Badge variant="secondary">
            Gates {delivery.gateRollup.status}{" "}
            {delivery.gateRollup.total
              ? `${delivery.gateRollup.passed}/${delivery.gateRollup.total}`
              : ""}
          </Badge>
        ) : null}
        {statusItems.map(([label, value]) => (
          <Badge key={label} variant="secondary">
            {label} {value}
          </Badge>
        ))}
        {threadItems.map(([label, value]) => (
          <Badge key={label} variant="outline" title={value ?? undefined}>
            {label}
          </Badge>
        ))}
        {delivery.smokeProof ? (
          <Badge
            variant={
              delivery.smokeProof.status === "passed" ? "default" : "destructive"
            }
          >
            Smoke {delivery.smokeProof.status ?? "recorded"}
          </Badge>
        ) : null}
      </div>
      <div className="grid gap-2 text-xs sm:grid-cols-2">
        {delivery.pr?.url ? (
          <a
            href={delivery.pr.url}
            className="truncate text-muted-foreground hover:text-foreground"
            target="_blank"
            rel="noreferrer"
          >
            PR #{delivery.pr.number}: {delivery.pr.title ?? "Untitled"}
          </a>
        ) : null}
        {delivery.previewUrl ? (
          <a
            href={delivery.previewUrl}
            className="truncate text-muted-foreground hover:text-foreground"
            target="_blank"
            rel="noreferrer"
          >
            Preview: {delivery.previewUrl}
          </a>
        ) : null}
      </div>
      {delivery.artifacts.length ? (
        <div className="grid gap-1 text-xs sm:grid-cols-2">
          {delivery.artifacts.slice(0, 4).map((artifact, index) => (
            <EvidenceLink key={index} value={artifact} />
          ))}
        </div>
      ) : null}
      {delivery.gates.length || blockers.length || delivery.blockerReason ? (
        <div className="grid gap-1 text-xs text-muted-foreground sm:grid-cols-2">
          {delivery.gates.slice(0, 4).map((gate, index) => (
            <span key={`gate-${index}`} className="truncate">
              {objectLabel(gate)}
            </span>
          ))}
          {blockers.map((blocker, index) => (
            <span key={`blocker-${index}`} className="truncate text-destructive">
              {blocker}
            </span>
          ))}
          {delivery.blockerReason ? (
            <span className="truncate text-destructive">
              {delivery.blockerReason}
            </span>
          ) : null}
        </div>
      ) : null}
      {smokeAcceptance.length ? (
        <div className="flex flex-wrap gap-1.5 text-[10px] text-muted-foreground">
          {smokeAcceptance.map(([key, passed]) => (
            <Badge key={key} variant={passed ? "secondary" : "destructive"}>
              {key.replaceAll("_", " ")}
            </Badge>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function WorkspaceRuns({ project }: { project: DeliveryProjectSummary }) {
  if (!project.latest_runs.length) {
    return (
      <div className="px-4 py-5 text-xs text-muted-foreground">
        No queue runs recorded yet.
      </div>
    )
  }
  return (
    <div className="divide-y divide-border">
      {project.latest_runs.map((run) => (
        <div
          key={run.id ?? `${run.provider}-${run.external_work_item_id}`}
          className="grid gap-2 px-4 py-3 text-xs sm:grid-cols-[1fr_100px_160px]"
        >
          <div className="min-w-0">
            <div className="truncate font-medium text-foreground">
              {text(run.title)}
            </div>
            <div className="mt-0.5 truncate text-muted-foreground">
              {text(run.provider)} / {text(run.external_work_item_id)}
            </div>
          </div>
          <div className="text-muted-foreground">{text(run.status)}</div>
          <div className="truncate text-muted-foreground">
            {text(run.updated_at)}
          </div>
          <DeliveryRunDetails delivery={run.delivery} />
        </div>
      ))}
    </div>
  )
}

function AutoModeTickSummary({
  result,
}: {
  result: DeliveryAutoModeTickResult | null
}) {
  if (!result) return null
  const pollErrors = result.poll?.errors ?? []
  const needsLinearToken = pollErrors.some((error) =>
    String(error.message ?? "")
      .toLowerCase()
      .includes("linear provider token")
  )
  return (
    <div className="space-y-3 px-4 pb-4 text-xs">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          ["poll", result.poll?.status ?? "not run"],
          ["queued", result.queued],
          ["launched", result.launched.length],
          ["skipped", result.skipped.length],
        ].map(([label, value]) => (
          <div key={label} className="rounded-md border border-border p-2">
            <div className="text-[10px] font-medium text-muted-foreground uppercase">
              {label}
            </div>
            <div className="mt-0.5 font-medium text-foreground">
              {String(value)}
            </div>
          </div>
        ))}
      </div>
      {pollErrors.length ? (
        <div className="space-y-1 text-destructive">
          {pollErrors.map((error, index) => (
            <div key={`${error.project_id ?? "poll"}-${index}`}>
              {error.message ?? "Auto-Mode poll failed."}
            </div>
          ))}
        </div>
      ) : null}
      {needsLinearToken ? (
        <a
          href="/my-settings?provider=linear"
          className="inline-flex text-destructive underline-offset-4 hover:underline"
        >
          Open Profile Settings to connect Linear
        </a>
      ) : null}
      {result.refused.length ? (
        <div className="space-y-1 text-muted-foreground">
          {result.refused.map((item, index) => (
            <div key={`${String(item.item_id ?? "refused")}-${index}`}>
              {String(item.reason ?? item.status ?? "Launch refused")}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function WorkspaceAutoModeSection({ projectId }: { projectId: string }) {
  const qc = useQueryClient()
  const [result, setResult] = useState<DeliveryAutoModeTickResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const runTick = useMutation({
    mutationFn: () => api.runWorkspaceAutoModeTick(projectId),
    onSuccess: (next) => {
      setResult(next)
      setError(null)
      void qc.invalidateQueries({ queryKey: ["deliveryProjects"] })
      void qc.invalidateQueries({
        queryKey: ["deliveryProjectReadiness", projectId],
      })
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <SettingsSection
      title="Auto-Mode"
      description="Poll Linear for this workspace and start eligible queued delivery work."
    >
      <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="text-xs text-muted-foreground">
          Runs one scoped tick for this workspace. Linear polling remains
          poll-only; delivery starts only for eligible queue items.
        </div>
        <Button
          size="sm"
          onClick={() => runTick.mutate()}
          disabled={runTick.isPending}
        >
          {runTick.isPending ? "Running…" : "Run Auto-Mode now"}
        </Button>
      </div>
      <AutoModeTickSummary result={result} />
      {error ? <p className="px-4 pb-4 text-xs text-destructive">{error}</p> : null}
    </SettingsSection>
  )
}

function ReadinessPanel({
  readiness,
}: {
  readiness?: DeliveryProjectReadiness
}) {
  if (!readiness) {
    return (
      <SettingsSection title="Readiness">
        <div className="p-4">
          <Skeleton className="h-28 w-full" />
        </div>
      </SettingsSection>
    )
  }
  const failed = readiness.checks.filter((check) => !check.ready)

  return (
    <SettingsSection
      title="Readiness"
      description={`Environment: ${readiness.environment}`}
    >
      <div className="border-b border-border p-4">
        <div className="flex items-center gap-2">
          <Badge variant={readiness.ready ? "default" : "destructive"}>
            {readiness.ready ? "Ready" : "Blocked"}
          </Badge>
          <span className="text-xs text-muted-foreground">
            {failed.length
              ? `${failed.length} readiness checks need attention.`
              : "All delivery readiness checks passed."}
          </span>
        </div>
      </div>
      <div className="grid gap-0 divide-y divide-border">
        {readiness.checks.map((check) => (
          <div
            key={check.key}
            className="grid gap-2 px-4 py-3 text-xs sm:grid-cols-[180px_80px_1fr_120px]"
          >
            <div className="font-medium text-foreground">{check.label}</div>
            <div
              className={check.ready ? "text-foreground" : "text-destructive"}
            >
              {check.ready ? "Ready" : "Blocked"}
            </div>
            <div className="min-w-0 text-muted-foreground">
              <div>{check.message}</div>
              {check.blockers.length ? (
                <div className="mt-1 space-y-0.5">
                  {check.blockers.map((blocker) => (
                    <div key={blocker.code ?? blocker.message}>
                      {blocker.message ?? blocker.code}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
            {!check.ready ? (
              <a
                href={check.action_href ?? `#${check.section}`}
                className="text-muted-foreground hover:text-foreground"
              >
                {check.action_label ?? "Open section"}
              </a>
            ) : (
              <span className="text-muted-foreground">-</span>
            )}
          </div>
        ))}
      </div>
    </SettingsSection>
  )
}

function WorkspaceDetail({
  project,
  readiness,
}: {
  project: DeliveryProjectSummary
  readiness?: DeliveryProjectReadiness
}) {
  const vcsConfig = project.vcs.config ?? {}
  const sandboxRuntime =
    typeof project.sandbox_profile.runtime === "object" &&
    project.sandbox_profile.runtime !== null
      ? (project.sandbox_profile.runtime as Record<string, unknown>)
      : {}

  return (
    <>
      <SettingsSection title="Overview">
        <div
          id="overview"
          className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between"
        >
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-sm font-medium">{project.name}</h2>
              <StatusBadge project={project} />
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {project.project_id}
            </p>
          </div>
          <Link to="/workspaces" className="text-xs text-muted-foreground">
            Back to workspaces
          </Link>
        </div>
        <KeyValueGrid
          items={[
            ["tracker", text(project.tracker.provider)],
            ["vcs", text(project.vcs.provider)],
            ["repository", `${text(vcsConfig.owner)}/${text(vcsConfig.repo)}`],
            ["base_branch", valueText(project.branch_policy.base_branch)],
            ["auto_mode", autoModeState(project)],
            ["preview_url", valueText(sandboxRuntime.preview_url)],
            ["members", project.member_logins.length],
          ]}
        />
      </SettingsSection>

      <ReadinessPanel readiness={readiness} />

      <WorkspaceAutoModeSection projectId={project.project_id} />

      <WorkspaceRepositoriesSection projectId={project.project_id} />

      <WorkspaceTicketIntakeSection projectId={project.project_id} />

      <WorkspaceCredentialsSection projectId={project.project_id} />

      <WorkspaceModelEndpointsSection projectId={project.project_id} />

      <WorkspaceModelRoutingSection projectId={project.project_id} />

      <WorkspaceDeliveryPolicySection projectId={project.project_id} />

      <SettingsSection title="Policies">
        <div id="policies">
          <KeyValueGrid
            items={[
              ...entries(project.gate_policy),
              ...entries(project.merge_policy),
              ...entries(project.run_limits),
            ]}
          />
        </div>
      </SettingsSection>

      <SettingsSection title="Members">
        <TokenList items={project.member_logins} />
      </SettingsSection>

      <SettingsSection title="Runs">
        <WorkspaceRuns project={project} />
      </SettingsSection>
    </>
  )
}

function WorkspaceDetailPage() {
  const session = useSession()
  const { projectId } = Route.useParams()
  const projects = useQuery({
    queryKey: ["deliveryProjects"],
    queryFn: api.listDeliveryProjects,
    enabled: !!session.data,
  })
  const readiness = useQuery({
    queryKey: ["deliveryProjectReadiness", projectId],
    queryFn: () => api.getDeliveryProjectReadiness(projectId),
    enabled: !!session.data,
  })

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    )
  }
  if (!session.data) return <Navigate to="/login" />

  const project = projects.data?.items.find(
    (item) => item.project_id === projectId
  )

  return (
    <AppShell
      user={session.data}
      title="Agent Workspace"
      description="Workspace configuration, delivery policy, members, and latest queue runs."
      className="max-w-5xl"
    >
      {projects.isLoading ? (
        <SettingsSection title="Workspace">
          <div className="p-4">
            <Skeleton className="h-40 w-full" />
          </div>
        </SettingsSection>
      ) : project ? (
        <WorkspaceDetail project={project} readiness={readiness.data} />
      ) : (
        <SettingsSection title="Workspace">
          <div className="px-4 py-6 text-xs text-muted-foreground">
            Workspace not found for this account.
          </div>
        </SettingsSection>
      )}
    </AppShell>
  )
}
