import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { AgentSettings } from "@/features/settings/components/AgentSettings"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/agent")({
  component: () => (
    <SettingsPage
      title="Agent"
      description="Your defaults for agent runs you trigger. They override the workspace defaults for you only."
    >
      <AgentSettings />
    </SettingsPage>
  ),
  head: () => ({ meta: [{ title: pageTitle("Agent") }] }),
})
