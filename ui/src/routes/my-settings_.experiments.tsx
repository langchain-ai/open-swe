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
      <SettingsSection title="Agent">
        <ProfileSwitchRow
          field="experimental_task_coordination"
          label="Asynchronous task coordination (experimental)"
          description="Let your agent delegate to workers that run independently. Turning this off prevents new workers; existing workers can finish and remain available to message or cancel."
        />
        <ProfileSwitchRow
          field="experimental_background_callbacks"
          label="Background command callbacks (experimental)"
          description="Background commands you start report completion from the sandbox instead of being polled every minute."
        />
        <ProfileSwitchRow
          field="prefer_tools_in_sandbox"
          label="Prefer tools through the sandbox"
          description="The agent calls MCP integrations and large-result lookups through the sandbox tools endpoint so it can filter their output. Applies to threads you start afterwards."
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
