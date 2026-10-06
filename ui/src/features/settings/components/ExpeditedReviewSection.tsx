import { SettingSection } from "@langchain/gtm-platform-design-system/patterns/setting-section"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Switch } from "@langchain/gtm-platform-design-system/ui/switch"
import {
  useScopedSettings,
  type SettingsScope,
} from "@/features/settings/lib/settingsScope"
import { TierRow } from "./WorkspaceSettingsSections"

export function ExpeditedReviewSection({ scope }: { scope: SettingsScope }) {
  const settings = useScopedSettings(scope)
  return (
    <SettingSection
      contained
      title="Expedited Slack review"
      description="Lets the agent ask for a pull request of at most 20 changed lines outside tests to be approved and merged from its Slack thread. The card appears only once every check GitHub requires is green and every review is clean; two people with write access approve, and their clicks become real GitHub reviews. When on, the agent gets the expedite_pr_approval tool in Slack threads. Off by default."
    >
      <TierRow
        settings={settings}
        fields={["expedited_review_enabled"]}
        label="Allow expedited Slack review"
        tag={
          <Badge tier="notable" tone="info">
            Experimental
          </Badge>
        }
        description="When off, open approval cards are withdrawn and no new ones are posted."
        density="compact"
        control={(slot) => (
          <Switch
            id={slot.id}
            aria-describedby={slot.describedById}
            checked={!!settings.data?.expedited_review_enabled}
            onCheckedChange={(next) =>
              settings.save({ expedited_review_enabled: next })
            }
            disabled={!settings.data}
          />
        )}
      />
    </SettingSection>
  )
}
