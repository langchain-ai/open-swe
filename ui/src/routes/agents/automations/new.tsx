import { createFileRoute } from "@tanstack/react-router"

import { AutomationEditor } from "@/components/agents/AutomationEditor"

export const Route = createFileRoute("/agents/automations/new")({
  component: NewAutomationPage,
})

function NewAutomationPage() {
  return <AutomationEditor mode="create" />
}
