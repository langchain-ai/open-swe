import { SettingsSection } from "@/components/AppShell"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import {
  useScopedSettings,
  type SettingsScope,
} from "@/features/settings/lib/settingsScope"
import { TierRow } from "./WorkspaceSettingsSections"

export function ExpeditedReviewSection({ scope }: { scope: SettingsScope }) {
  const settings = useScopedSettings(scope)
  return (
    <SettingsSection
      title={
        <span className="inline-flex items-center gap-2">
          Expedited Slack review
          <Badge variant="outline">Experimental</Badge>
        </span>
      }
      description="Lets the agent ask for a pull request of at most 20 changed lines outside tests to be approved and merged from its Slack thread. The card appears only once every check GitHub requires is green and every review is clean; two people with write access approve, and their clicks become real GitHub reviews. Off by default."
    >
      <div className="divide-y divide-border">
        <TierRow
          settings={settings}
          fields={["expedited_review_enabled"]}
          label="Allow expedited Slack review"
          description="When on, the agent gets the expedite_pr_approval tool in Slack threads. When off, open approval cards are withdrawn and no new ones are posted."
          control={
            <Switch
              checked={!!settings.data?.expedited_review_enabled}
              onCheckedChange={(next) =>
                settings.save({ expedited_review_enabled: next })
              }
              disabled={!settings.data}
            />
          }
        />
      </div>
      {settings.error && (
        <p className="px-4 pb-3 text-xs text-destructive">{settings.error}</p>
      )}
    </SettingsSection>
  )
}
