import { useQuery } from "@tanstack/react-query"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"

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
          ? "View the environments available to new agent threads. To create or edit one, start a new agent thread, open the + menu, enable admin mode, and ask Open SWE to make the change."
          : "View the environments available to new agent threads. To create or edit one, ask a workspace admin to start an admin thread and ask Open SWE to make the change."
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
          <SettingsRow
            key={environment.slug}
            label={environment.name}
            description={
              environment.slug === options.default_slug
                ? "Default environment"
                : undefined
            }
            control={
              <span className="text-xs text-muted-foreground">
                {environment.has_snapshot ? "Snapshot ready" : "No snapshot"}
              </span>
            }
          />
        ))
      )}
    </SettingsSection>
  )
}
