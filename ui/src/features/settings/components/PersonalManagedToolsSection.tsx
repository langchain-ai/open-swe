import { SettingsSection } from "@/components/AppShell"
import { Switch } from "@/components/ui/switch"
import {
  useScopedSettings,
  type SettingsScope,
} from "@/features/settings/lib/settingsScope"
import { TierRow } from "./WorkspaceSettingsSections"

export function PersonalManagedToolsSection({
  scope,
}: {
  scope: SettingsScope
}) {
  const settings = useScopedSettings(scope)
  return (
    <SettingsSection
      title="Personal managed tools"
      description="People can turn on LangSmith Managed Tools servers in their own settings and connect with their own accounts. Those tools load only in the owner's private threads. On by default."
    >
      <div className="divide-y divide-border">
        <TierRow
          settings={settings}
          fields={["personal_managed_tools_enabled"]}
          label="Allow personal managed tools"
          description="When off, private threads here load no one's managed tools; personal connections and selections are kept."
          control={
            <Switch
              checked={settings.data?.personal_managed_tools_enabled !== false}
              onCheckedChange={(next) =>
                settings.save({ personal_managed_tools_enabled: next })
              }
              disabled={!settings.data}
            />
          }
        />
      </div>
    </SettingsSection>
  )
}
