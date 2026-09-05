import { useQuery } from "@tanstack/react-query"

import { SettingsSection } from "@/components/AppShell"
import { Skeleton } from "@/components/ui/skeleton"
import {
  api,
  type EnvironmentOption,
  type EnvironmentRefreshStatus,
} from "@/lib/api"
import { formatRelativeTime } from "@/lib/utils"

const REFRESH_LABEL: Record<EnvironmentRefreshStatus, string> = {
  never: "Never refreshed",
  refreshing: "Refreshing…",
  success: "Refreshed",
  failed: "Refresh failed",
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

function EnvironmentRow({
  environment,
  isDefault,
}: {
  environment: EnvironmentOption
  isDefault: boolean
}) {
  const status = environment.refresh_status ?? "never"
  const when = refreshedAt(environment.refresh_finished_at)
  const log = environment.refresh_log_excerpt
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
          {REFRESH_LABEL[status]}
          {status !== "refreshing" && when ? ` ${when}` : ""}
        </span>
      </div>
      {environment.refresh_error && (
        <p className="text-xs/relaxed text-destructive">
          {environment.refresh_error}
        </p>
      )}
      {log && (
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
          ? "Each environment is rebuilt nightly from its setup script. To create or edit one, start a new agent thread, open the + menu, enable admin mode, and ask Open SWE to make the change."
          : "Each environment is rebuilt nightly from its setup script. To create or edit one, ask a workspace admin to start an admin thread and ask Open SWE to make the change."
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
          />
        ))
      )}
    </SettingsSection>
  )
}
