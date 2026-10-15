import { useQuery } from "@tanstack/react-query"
import { Select } from "@langchain/macaw-components/Select"

import { SettingsSection } from "@/components/AppShell"
import {
  INSTANCE_SCOPE,
  useScopedSettings,
} from "@/features/settings/lib/settingsScope"
import { api } from "@/lib/api"
import { TierRow } from "./WorkspaceSettingsSections"

const NONE = "none"

/** Instance admins pick the LangSmith Managed Tools tool kit every private thread loads. */
export function ManagedToolsGatewaySection() {
  const settings = useScopedSettings(INSTANCE_SCOPE)
  const gateways = useQuery({
    queryKey: ["managedToolsGateways"],
    queryFn: api.listManagedToolsGateways,
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
      ? [{ value: current, label: "Configured tool kit" }]
      : []),
  ]

  return (
    <SettingsSection
      title="Managed Tools"
      description="Pick a tool kit from LangSmith Managed Tools. Private threads in every workspace offer its tools, called with each person's own LangSmith connection; they never load in threads other people can prompt. Build and edit tool kits under LangSmith Settings > Tools."
    >
      <div className="divide-y divide-default">
        <TierRow
          settings={settings}
          fields={["managed_tools_gateway_id"]}
          label="Tool kit"
          description={
            gateways.isError
              ? `Tool kits could not be listed: ${gateways.error.message}`
              : "People connect the services it needs under My settings > Connections."
          }
          control={
            <Select
              aria-label="Tool kit"
              options={items}
              value={current ?? NONE}
              onChange={(next) =>
                next &&
                settings.save({
                  managed_tools_gateway_id: next === NONE ? null : next,
                })
              }
              disabled={!settings.data || gateways.isLoading}
              triggerClassName="w-64"
            />
          }
        />
      </div>
    </SettingsSection>
  )
}
