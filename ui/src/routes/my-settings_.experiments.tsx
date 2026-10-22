import { createFileRoute } from "@tanstack/react-router"
import { Button } from "@langchain/macaw-components/Button"

import {
  SettingsPage,
  SettingsRow,
  SettingsSection,
} from "@/components/AppShell"
import { AssistantUiPreference } from "@/features/settings/components/AssistantUiPreference"
import { ProfileSwitchRow } from "@/features/settings/components/ProfileSwitchRow"
import { usePatchProfile, useProfile } from "@/lib/profile"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/experiments")({
  component: ExperimentsPage,
  head: () => ({ meta: [{ title: pageTitle("Experiments") }] }),
})

function ExperimentsPage() {
  const profile = useProfile()
  const patch = usePatchProfile()
  const alwaysAllowed = profile.data?.act_as_always_allowed && !patch.isPending
  return (
    <SettingsPage
      title="Experiments"
      description="Features under test. They may change or go away."
    >
      <SettingsSection title="Interface">
        <AssistantUiPreference />
      </SettingsSection>
      <SettingsSection title="Slack">
        <ProfileSwitchRow
          field="concierge_mode"
          label="Concierge mode"
          description="Your whole DM with Open SWE becomes one private thread it always answers in, instead of a new thread per message."
        />
        <ProfileSwitchRow
          field="pr_review_links"
          label="Open pull requests in Open SWE"
          description="Pull request links Open SWE posts for you open its review page instead of GitHub."
        />
        <ProfileSwitchRow
          field="pr_failure_reactions"
          label="React to failing checks"
          description="Add ⚠️ to watched pull request posts when checks fail on a pull request you own."
        />
      </SettingsSection>
      <SettingsSection title="Agent">
        <ProfileSwitchRow
          field="experimental_background_callbacks"
          label="Background command callbacks"
          description="Background commands you start report completion from the sandbox instead of being polled every minute."
        />
        <ProfileSwitchRow
          field="experimental_monitor"
          label="Monitors"
          description="Give your agent a monitor tool: it runs a long-lived sandbox command and is woken with each line its filter picks out, such as errors in a log. Applies on the next run."
        />
        <SettingsRow
          label="PR consent"
          description={
            alwaysAllowed
              ? "You chose Always allow, so Open SWE is not asking for approval."
              : "Approval is required by default for shared-thread and relayed PR actions on your behalf."
          }
          control={
            <Button
              variant="outlined"
              size="sm"
              disabled={!profile.isSuccess || !alwaysAllowed}
              onClick={() =>
                patch.patch({
                  experimental_act_as_approval:
                    !profile.data?.experimental_act_as_approval,
                })
              }
            >
              Require approval
            </Button>
          }
        />
      </SettingsSection>
    </SettingsPage>
  )
}
