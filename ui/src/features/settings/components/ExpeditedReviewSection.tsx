import { Badge } from "@langchain/macaw-components/Badge"
import { Switch } from "@langchain/macaw-components/Switch"

import { SettingsSection } from "@/components/AppShell"
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
        <span className="inline-flex items-center gap-space-2">
          Expedited Slack review
          <Badge color="secondary" size="xs">
            Experimental
          </Badge>
        </span>
      }
      description="Lets the agent ask for a pull request of at most 20 changed lines outside tests to be approved and merged from its Slack thread. The card appears only once every check GitHub requires is green and every review is clean; two people with write access approve, and their clicks become real GitHub reviews. Off by default."
    >
      <div className="divide-y divide-default">
        <TierRow
          settings={settings}
          fields={["expedited_review_enabled"]}
          label="Allow expedited Slack review"
          description="When on, the agent gets the expedite_pr_approval tool in Slack threads. When off, open approval cards are withdrawn and no new ones are posted."
          control={
            <Switch
              aria-label="Allow expedited Slack review"
              checked={!!settings.data?.expedited_review_enabled}
              onChange={(next) =>
                settings.save({ expedited_review_enabled: next })
              }
              disabled={!settings.data}
            />
          }
        />
      </div>
    </SettingsSection>
  )
}
