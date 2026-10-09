import { createFileRoute } from "@tanstack/react-router"
import { Switch } from "@langchain/macaw-components/Switch"

import {
  SettingsPage,
  SettingsRow,
  SettingsSection,
} from "@/components/AppShell"
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
      <SettingsSection title="Agent">
        <SettingsRow
          htmlFor="experimental_task_coordination"
          label="Asynchronous task coordination (experimental) — ALWAYS DISABLED"
          description="Disabled for everyone, regardless of your saved preference."
          control={
            <Switch
              id="experimental_task_coordination"
              aria-label="Asynchronous task coordination (experimental)"
              checked={profile.data?.experimental_task_coordination ?? false}
              onChange={() => {}}
              disabled
            />
          }
        />
        <ProfileSwitchRow
          field="experimental_background_callbacks"
          label="Background command callbacks (experimental)"
          description="Background commands you start report completion from the sandbox instead of being polled every minute."
        />
        <ProfileSwitchRow
          field="experimental_mcp_ptc"
          label="Programmatic tool calling (experimental)"
          description="Let the agent call integrations and file read/write tools from JavaScript. Applies to runs in threads you originally started."
        />
        <ProfileSwitchRow
          field="prefer_tools_in_sandbox"
          label="Prefer tools through the sandbox"
          description="The agent calls MCP integrations and large-result lookups through the sandbox tools endpoint so it can filter their output. Applies to threads you start afterwards."
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
