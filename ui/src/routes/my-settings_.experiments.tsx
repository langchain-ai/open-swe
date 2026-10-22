import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage, SettingsSection } from "@/components/AppShell"
import { AssistantUiPreference } from "@/features/settings/components/AssistantUiPreference"
import { ProfileSwitchRow } from "@/features/settings/components/ProfileSwitchRow"
import { useProfile } from "@/lib/profile"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/experiments")({
  component: ExperimentsPage,
  head: () => ({ meta: [{ title: pageTitle("Experiments") }] }),
})

function ExperimentsPage() {
  const profile = useProfile()
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
        <ProfileSwitchRow
          field="experimental_act_as_approval"
          label="Approve PRs opened as you"
          description={
            profile.data?.act_as_always_allowed
              ? "You chose Always allow, so Open SWE is not asking. Switch this off and on to be asked again."
              : "In Slack threads with more than one person, Open SWE DMs you for approval before opening a PR under your name."
          }
        />
      </SettingsSection>
    </SettingsPage>
  )
}
