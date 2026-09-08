import { useQuery } from "@tanstack/react-query"

import { SettingsSection } from "@/components/AppShell"
import { Skeleton } from "@/components/ui/skeleton"
import {
  api,
  type EnvironmentOption,
  type EnvironmentRefreshStatus,
  type EnvironmentRefreshStep,
} from "@/lib/api"
import { formatRelativeTime } from "@/lib/utils"

const REFRESH_LABEL: Record<EnvironmentRefreshStatus, string> = {
  never: "Never refreshed",
  refreshing: "Refreshing…",
  success: "Refreshed",
  failed: "Refresh failed",
}

// A nightly rebuild from the base image and an hourly update of the current
// snapshot read differently to a person deciding whether to trust the image.
function refreshLabel(
  status: EnvironmentRefreshStatus,
  kind: EnvironmentOption["refresh_kind"]
): string {
  if (status === "success" && kind === "update") return "Updated"
  if (status === "success" && kind === "full") return "Rebuilt"
  if (status === "refreshing" && kind === "update") return "Updating…"
  if (status === "refreshing" && kind === "full") return "Rebuilding…"
  return REFRESH_LABEL[status]
}

const REFRESH_CLASS: Record<EnvironmentRefreshStatus, string> = {
  never: "text-muted-foreground",
  refreshing: "text-muted-foreground",
  success: "text-muted-foreground",
  failed: "text-destructive",
}

function refreshedAt(timestamp: string | null | undefined): string | null {
  if (!timestamp) return null
  const parsed = Date.parse(timestamp)
  return Number.isNaN(parsed) ? null : formatRelativeTime(parsed)
}

const STEP_MARK: Record<EnvironmentRefreshStep["status"], string> = {
  running: "…",
  success: "✓",
  failed: "✕",
}

const STEP_CLASS: Record<EnvironmentRefreshStep["status"], string> = {
  running: "border-border text-foreground",
  success: "border-border text-muted-foreground",
  failed: "border-destructive/40 text-destructive",
}

// A rebuild runs for minutes to an hour; which stage it reached is the only
// thing that separates slow from wedged while it is still going.
function RefreshSteps({ steps }: { steps: Array<EnvironmentRefreshStep> }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {steps.map((step) => (
        <span
          key={step.label}
          className={`rounded-full border px-2 py-0.5 text-[11px] ${STEP_CLASS[step.status]}`}
        >
          {STEP_MARK[step.status]} {step.label}
          {step.exit_code ? ` (exit ${step.exit_code})` : ""}
        </span>
      ))}
    </div>
  )
}

function EnvironmentRow({
  environment,
  isDefault,
  isAdmin,
}: {
  environment: EnvironmentOption
  isDefault: boolean
  isAdmin: boolean
}) {
  const status = environment.refresh_status ?? "never"
  const when = refreshedAt(environment.refresh_finished_at)
  const log = environment.refresh_log_excerpt
  const steps = environment.refresh_steps ?? []
  const detail = [
    isDefault ? "Default environment" : null,
    environment.has_snapshot ? "Snapshot ready" : "No snapshot",
  ]
    .filter(Boolean)
    .join(" · ")

  return (
    <div className="flex flex-col gap-2 px-4 py-3.5">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-8">
        <div className="flex flex-col gap-1">
          <span className="text-sm/none font-medium text-foreground">
            {environment.name}
          </span>
          <span className="text-xs/relaxed text-muted-foreground">
            {detail}
          </span>
        </div>
        <span className={`text-xs sm:shrink-0 ${REFRESH_CLASS[status]}`}>
          {refreshLabel(status, environment.refresh_kind)}
          {status !== "refreshing" && when ? ` ${when}` : ""}
        </span>
      </div>
      {steps.length > 0 && <RefreshSteps steps={steps} />}
      {environment.refresh_error && (
        <p className="text-xs/relaxed text-destructive">
          {environment.refresh_error}
        </p>
      )}
      {/* The API omits the log for non-admins; this guard is defence in depth
          for a `bash -x` trace that can carry expanded credentials. */}
      {isAdmin && log && (
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer select-none">Refresh log</summary>
          <pre className="mt-2 max-h-64 overflow-auto rounded-md border border-border bg-muted/40 p-3 text-[11px] leading-relaxed whitespace-pre-wrap">
            {log}
          </pre>
        </details>
      )}
    </div>
  )
}

export function EnvironmentsSection({ isAdmin }: { isAdmin: boolean }) {
  const environments = useQuery({
    queryKey: ["environment-options"],
    queryFn: api.listEnvironmentOptions,
    staleTime: 60_000,
    refetchInterval: 5000,
  })
  const options = environments.data

  return (
    <SettingsSection
      title="Environments"
      description={
        isAdmin
          ? "Each environment is rebuilt nightly from its setup script and, while in use, updated hourly by its update script. To create or edit one, start a new agent thread, open the + menu, enable admin mode, and ask Open SWE to make the change."
          : "Each environment is rebuilt nightly from its setup script and, while in use, updated hourly by its update script. To create or edit one, ask a workspace admin to start an admin thread and ask Open SWE to make the change."
      }
    >
      {environments.isLoading ? (
        <div className="px-4 py-3.5">
          <Skeleton className="h-8 w-full" />
        </div>
      ) : environments.isError ? (
        <p className="px-4 py-3.5 text-xs text-destructive">
          Could not load environments.
        </p>
      ) : !options || options.environments.length === 0 ? (
        <p className="px-4 py-3.5 text-xs text-muted-foreground">
          No environments are configured.
        </p>
      ) : (
        options.environments.map((environment) => (
          <EnvironmentRow
            key={environment.slug}
            environment={environment}
            isDefault={environment.slug === options.default_slug}
            isAdmin={isAdmin}
          />
        ))
      )}
    </SettingsSection>
  )
}
