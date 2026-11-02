import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage, SettingsSection } from "@/components/AppShell"
import { StreamPreference } from "@/features/settings/components/StreamPreference"
import { ProfileSwitchRow } from "@/features/settings/components/ProfileSwitchRow"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/experiments")({
  component: ExperimentsPage,
  head: () => ({ meta: [{ title: pageTitle("Experiments") }] }),
})

function ExperimentsPage() {
  return (
    <SettingsPage
      title="Experiments"
      description="Features under test. They may change or go away."
    >
      <SettingsSection title="Interface">
        <StreamPreference />
        <ProfileSwitchRow
          field="experimental_fleet"
          label="LangSmith Agent Workspace"
          description="Use LangSmith Agent Workspace for your Open SWE agents, with one list of pinned, recent, and archived chats."
        />
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
          field="goal_mode"
          label="Goal mode"
          description="Open SWE keeps working without messaging you until it is blocked on you, or its pull request has passing checks, a few rounds of Open SWE review with the obvious findings fixed, and a walkthrough of the latest version."
        />
        <ProfileSwitchRow
          field="experimental_conditional_approval"
          label="Conditional approval"
          description="For pull requests you author, let Open SWE request approval of a succinct condition instead of showing the full diff when its review covers everything else. Off by default."
        />
        <ProfileSwitchRow
          field="experimental_memory"
          label="Memory"
          description="Threads you start include what your workspace's curator learned from everyone's memories, in place of your own memories it superseded."
        />
        <ProfileSwitchRow
          field="experimental_background_callbacks"
          label="Background command callbacks"
          description="Background commands you start report completion from the sandbox instead of being polled every minute."
        />
      </SettingsSection>
    </SettingsPage>
  )
}
