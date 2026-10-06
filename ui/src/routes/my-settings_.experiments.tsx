import { createFileRoute } from "@tanstack/react-router"

import { SettingSection } from "@langchain/gtm-platform-design-system/patterns/setting-section"

import { SettingsPage } from "@/components/AppShell"
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
      <AssistantUiPreference />
      <SettingSection title="Agent" contained>
        <ProfileSwitchRow
          field="experimental_background_callbacks"
          label="Background command callbacks (experimental)"
          description="Background commands you start report completion from the sandbox instead of being polled every minute."
        />
        <ProfileSwitchRow
          field="prefer_tools_in_sandbox"
          label="Prefer tools through the sandbox"
          description="MCP calls and large lookups run through the sandbox so the agent can filter their output. New threads only."
        />
        <ProfileSwitchRow
          field="experimental_act_as_approval"
          label="Approve PRs opened as you"
          description={
            profile.data?.act_as_always_allowed
              ? "You chose Always allow, so Open SWE is not asking. Toggle this to be asked again."
              : "In multi-person Slack threads, Open SWE DMs you before opening a PR under your name."
          }
        />
      </SettingSection>
    </SettingsPage>
  )
}
