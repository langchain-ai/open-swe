import { createFileRoute } from "@tanstack/react-router"

import { AgentInstructionsPanel } from "@/components/AgentInstructionsPanel"
import { AuthedAppShell } from "@/components/AppShell"

export const Route = createFileRoute("/agents_/instructions")({
  component: AgentInstructionsPage,
})

function AgentInstructionsPage() {
  return (
    <AuthedAppShell
      title="Repository Instructions"
      description="Per-repo custom instructions appended to the coding agent's system prompt for runs targeting that repository."
      backTo={{ to: "/cloud-agents", label: "Back to Open SWE Agent" }}
    >
      {() => (
        <div className="rounded-lg border border-border bg-card">
          <AgentInstructionsPanel />
        </div>
      )}
    </AuthedAppShell>
  )
}
