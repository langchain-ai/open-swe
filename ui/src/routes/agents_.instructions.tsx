import { createFileRoute } from "@tanstack/react-router"

import { AgentInstructionsPanel } from "@/components/AgentInstructionsPanel"
import { SettingsPage } from "@/components/AppShell"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/agents_/instructions")({
  head: () => ({ meta: [{ title: pageTitle("Repository instructions") }] }),
  component: () => (
    <SettingsPage fill>
      <AgentInstructionsPanel />
    </SettingsPage>
  ),
})
