import { createFileRoute } from "@tanstack/react-router"

import { AutomationEditor } from "@/components/agents/AutomationEditor"
import { automationTemplateById } from "@/lib/agents/automation-templates"

interface NewAutomationSearch {
  template?: string
}

export const Route = createFileRoute("/agents/automations/new")({
  validateSearch: (search: Record<string, unknown>): NewAutomationSearch => ({
    template: typeof search.template === "string" ? search.template : undefined,
  }),
  component: NewAutomationPage,
})

function NewAutomationPage() {
  const { template } = Route.useSearch()
  return (
    <AutomationEditor
      mode="create"
      template={automationTemplateById(template)}
    />
  )
}
