import { createFileRoute } from "@tanstack/react-router"

import { AgentInstructionsPanel } from "@/components/AgentInstructionsPanel"
import { SettingsPage } from "@/components/AppShell"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/agents_/instructions")({
  head: () => ({ meta: [{ title: pageTitle("Repository instructions") }] }),
  component: () => (
    <SettingsPage
      title="Repository instructions"
      description="Per-repository instructions added to the agent's system prompt for runs in that repository."
    >
      <div className="rounded-compact border border-line bg-panel">
        <AgentInstructionsPanel />
      </div>
    </SettingsPage>
  ),
})
