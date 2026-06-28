import { Link, Navigate, createFileRoute } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"

import type { DeliveryProjectSummary } from "@/lib/api"
import { AppShell, SettingsSection } from "@/components/AppShell"
import { Badge } from "@/components/ui/badge"
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
        </div>
      ))}
    </div>
  )
}

function WorkspaceDetail({ project }: { project: DeliveryProjectSummary }) {
  const trackerConfig = project.tracker.config ?? {}
  const vcsConfig = project.vcs.config ?? {}
  const sandboxRuntime =
    typeof project.sandbox_profile.runtime === "object" &&
    project.sandbox_profile.runtime !== null
      ? (project.sandbox_profile.runtime as Record<string, unknown>)
      : {}

  return (
    <>
      <SettingsSection title="Overview">
        <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between">
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

      <SettingsSection title="Repositories">
        <KeyValueGrid items={entries(vcsConfig)} />
      </SettingsSection>

      <SettingsSection title="Ticket Intake">
        <KeyValueGrid
          items={[
            ...entries(trackerConfig),
            ...entries(project.queue_eligibility_policy),
          ]}
        />
      </SettingsSection>

      <SettingsSection title="Credentials">
        <KeyValueGrid
          items={[
            ["project_secrets", "Project-scoped"],
            ["provider_tokens", "Per user"],
            ["ai_hub", "Project readiness gated"],
          ]}
        />
      </SettingsSection>

      <SettingsSection title="Models">
        <TokenList items={project.delivery_modes} />
      </SettingsSection>

      <SettingsSection title="Policies">
        <KeyValueGrid
          items={[
            ...entries(project.gate_policy),
            ...entries(project.merge_policy),
            ...entries(project.run_limits),
          ]}
        />
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
        <WorkspaceDetail project={project} />
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
