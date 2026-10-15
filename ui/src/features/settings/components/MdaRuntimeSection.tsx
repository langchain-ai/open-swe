import { Badge } from "@langchain/macaw-components/Badge"
import { Switch } from "@langchain/macaw-components/Switch"

import { SettingsSection } from "@/components/AppShell"
import {
  useScopedSettings,
  type SettingsScope,
} from "@/features/settings/lib/settingsScope"
import { useProfile } from "@/lib/profile"
import { TierRow } from "./WorkspaceSettingsSections"

/** Where threads no person starts run, when a Managed Deep Agents deployment is configured. */
export function MdaRuntimeSection({ scope }: { scope: SettingsScope }) {
  const settings = useScopedSettings(scope)
  const profile = useProfile()
  if (!profile.data?.mda_available) return null
  return (
    <SettingsSection
      title={
        <span className="inline-flex items-center gap-space-2">
          Managed Deep Agents
          <Badge color="secondary" size="xs">
            Experimental
          </Badge>
        </span>
      }
      description="Threads that no person starts, such as API key, GitHub Actions workflow and system review threads, run their model loop on the Managed Deep Agents deployment. Each person picks for their own threads under My settings → Experiments."
    >
      <div className="divide-y divide-default">
        <TierRow
          settings={settings}
          fields={["run_on_mda"]}
          label="Run automation threads on Managed Deep Agents"
          description="Applies to threads started afterwards; existing threads stay where they are."
          control={
            <Switch
              aria-label="Run automation threads on Managed Deep Agents"
              checked={!!settings.data?.run_on_mda}
              onChange={(next) => settings.save({ run_on_mda: next })}
              disabled={!settings.data}
            />
          }
        />
      </div>
    </SettingsSection>
  )
}
