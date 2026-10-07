import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { AgentSettings } from "@/features/settings/components/AgentSettings"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/agent")({
  component: () => (
    <SettingsPage
      title="Agent"
      description="Your model, run defaults, and personal instructions for the agent. These settings apply only to you."
    >
      <AgentSettings />
    </SettingsPage>
  ),
  head: () => ({ meta: [{ title: pageTitle("Agent") }] }),
})
