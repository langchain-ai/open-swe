import { useQuery } from "@tanstack/react-query"
import { Select } from "@langchain/macaw-components/Select"

import { SettingsSection } from "@/components/AppShell"
import {
  useScopedSettings,
  type SettingsScope,
} from "@/features/settings/lib/settingsScope"
import { api } from "@/lib/api"
import { TierRow } from "./WorkspaceSettingsSections"

const NONE = "none"

/** Admins pick the LangSmith Managed Tools gateway this workspace's private threads load. */
export function ManagedToolsGatewaySection({
  scope,
  canEdit,
}: {
  scope: SettingsScope
  canEdit: boolean
}) {
  const settings = useScopedSettings(scope)
  const gateways = useQuery({
    queryKey: ["managedToolsGateways"],
    queryFn: api.listManagedToolsGateways,
    enabled: canEdit,
    retry: false,
  })
  const current = settings.data?.managed_tools_gateway_id ?? null
  const items = [
    { value: NONE, label: "None" },
    ...(gateways.data ?? []).map((gateway) => ({
      value: gateway.id,
      label: `${gateway.name} · ${gateway.tool_count} tools`,
    })),
    // Keep a saved gateway selectable even when the list can't be read.
    ...(current && !gateways.data?.some((gateway) => gateway.id === current)
      ? [{ value: current, label: "Configured gateway" }]
      : []),
  ]

  return (
    <SettingsSection
      title="Managed Tools"
      description="Pick a LangSmith Managed Tools gateway. Private threads in this workspace offer its tools, called with each person's own LangSmith connection; they never load in threads other people can prompt. Build and edit gateways under LangSmith Settings > Tools."
    >
      <div className="divide-y divide-default">
        <TierRow
          settings={settings}
          fields={["managed_tools_gateway_id"]}
          label="Gateway"
          description={
            gateways.isError
              ? `Gateways could not be listed: ${gateways.error.message}`
              : "People connect the services it needs under My settings > Connections."
          }
          control={
            <Select
              aria-label="Gateway"
              options={items}
              value={current ?? NONE}
              onChange={(next) =>
                next &&
                settings.save({
                  managed_tools_gateway_id: next === NONE ? null : next,
                })
              }
              disabled={!canEdit || !settings.data || gateways.isLoading}
              triggerClassName="w-64"
            />
          }
        />
      </div>
    </SettingsSection>
  )
}
