import { Link, Navigate, createFileRoute } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"

import type { DeliveryProjectSummary } from "@/lib/api"
import { AppShell, SettingsSection } from "@/components/AppShell"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/workspaces")({
  component: AgentWorkspacesPage,
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
  return text(value)
}

function list(value: unknown): Array<string> {
  return Array.isArray(value)
    ? value.filter(
        (item): item is string =>
          typeof item === "string" && Boolean(item.trim())
      )
    : []
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

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] font-medium text-muted-foreground uppercase">
        {label}
      </dt>
      <dd className="mt-0.5 truncate text-xs text-foreground">{value}</dd>
    </div>
  )
}

function TokenList({ items }: { items: Array<string> }) {
  if (!items.length) {
    return (
      <span className="text-xs text-muted-foreground">None configured</span>
    )
  }
  return (
    <div className="flex flex-wrap gap-1.5">
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

function WorkspaceCard({ project }: { project: DeliveryProjectSummary }) {
  const trackerConfig = project.tracker.config ?? {}
  const vcsConfig = project.vcs.config ?? {}
  const sandboxRuntime =
    typeof project.sandbox_profile.runtime === "object" &&
    project.sandbox_profile.runtime !== null
      ? (project.sandbox_profile.runtime as Record<string, unknown>)
      : {}
  const blockingGates = list(project.gate_policy.blocking_gates)
  const advisoryGates = list(project.gate_policy.advisory_gates)

  const latestRun = project.latest_runs[0]

  return (
    <Link
      to="/workspaces/$projectId"
      params={{ projectId: project.project_id }}
      className="block border-b border-border px-4 py-4 transition-colors last:border-b-0 hover:bg-muted/30"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-medium">{project.name}</h3>
            <StatusBadge project={project} />
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {project.project_id}
          </p>
        </div>
        <TokenList items={project.delivery_modes} />
      </div>

      <dl className="mt-4 grid gap-3 sm:grid-cols-3">
        <Detail label="Tracker" value={text(project.tracker.provider)} />
        <Detail
          label="Tracker scope"
          value={valueText(
            trackerConfig.project_id ?? trackerConfig.project_ids
          )}
        />
        <Detail label="VCS" value={text(project.vcs.provider)} />
        <Detail
          label="Repository"
          value={`${text(vcsConfig.owner)}/${text(vcsConfig.repo)}`}
        />
        <Detail
          label="Base branch"
          value={text(project.branch_policy.base_branch)}
        />
        <Detail
          label="Branch prefix"
          value={text(project.branch_policy.branch_prefix)}
        />
        <Detail label="Auto-Mode" value={autoModeState(project)} />
        <Detail
          label="Sandbox"
          value={text(project.sandbox_profile.provider)}
        />
        <Detail label="Preview" value={text(sandboxRuntime.preview_url)} />
        <Detail label="Members" value={String(project.member_logins.length)} />
        <Detail
          label="Latest run"
          value={
            latestRun
              ? `${text(latestRun.status)}: ${text(latestRun.title)}`
              : "No runs yet"
          }
        />
      </dl>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <h4 className="text-[10px] font-medium text-muted-foreground uppercase">
            Blocking gates
          </h4>
          <TokenList items={blockingGates} />
        </div>
        <div className="space-y-2">
          <h4 className="text-[10px] font-medium text-muted-foreground uppercase">
            Advisory gates
          </h4>
          <TokenList items={advisoryGates} />
        </div>
      </div>
    </Link>
  )
}

function AgentWorkspacesPage() {
  const session = useSession()
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

  return (
    <AppShell
      user={session.data}
      title="Agent Workspaces"
      description="Project delivery configuration, runtime gates, and access scope."
      className="max-w-5xl"
    >
      <SettingsSection title="Workspaces">
        {projects.isLoading ? (
          <div className="p-4">
            <Skeleton className="h-32 w-full" />
          </div>
        ) : projects.data?.items.length ? (
          projects.data.items.map((project) => (
            <WorkspaceCard key={project.project_id} project={project} />
          ))
        ) : (
          <div className="px-4 py-6 text-xs text-muted-foreground">
            No workspaces available for this account.
          </div>
        )}
      </SettingsSection>
    </AppShell>
  )
}
