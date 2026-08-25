import { createFileRoute } from "@tanstack/react-router"

import { AutomationEditor } from "@/features/automations/components/AutomationEditor"
import { automationTemplateById } from "@/features/automations/lib/automation-templates"

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
