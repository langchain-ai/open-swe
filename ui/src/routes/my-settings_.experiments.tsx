import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage, SettingsSection } from "@/components/AppShell"
import { StreamPreference } from "@/features/settings/components/StreamPreference"
import { ProfileSwitchRow } from "@/features/settings/components/ProfileSwitchRow"
import { useProfile } from "@/lib/profile"
import { appDistribution, pageTitle } from "@/lib/pageTitle"

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
        {appDistribution.isDesktop && (
          <ProfileSwitchRow
            field="experimental_agent_workspace"
            label="General-purpose interface"
            description="Use the chat-focused interface for any kind of work. Turn off to use the coding interface. This only changes the desktop app; existing chats keep their agent experience."
            fallback={true}
          />
        )}
        <StreamPreference />
      </SettingsSection>
      <SettingsSection title="Slack">
        <ProfileSwitchRow
          field="concierge_mode"
          label="Concierge mode"
          description="Your whole DM with Open SWE becomes one private thread it always answers in, instead of a new thread per message."
        />
        <ProfileSwitchRow
          field="pr_failure_reactions"
          label="React to failing checks"
          description="Add ⚠️ to watched pull request posts when checks fail on a pull request you own."
        />
      </SettingsSection>
      <SettingsSection title="Agent">
        <ProfileSwitchRow
          field="experimental_memory"
          label="Memory"
          description="Threads you start include what your workspace's curator learned from everyone's threads, and what you offer the workspace to remember in your private threads applies to your own runs until the curator processes it."
        />
        <ProfileSwitchRow
          field="experimental_background_callbacks"
          label="Background command callbacks"
          description="Background commands you start report completion from the sandbox instead of being polled every minute."
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
