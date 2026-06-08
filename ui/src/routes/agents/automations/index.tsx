import { createFileRoute } from "@tanstack/react-router"

import { AutomationsList } from "@/components/agents/AutomationsList"

export const Route = createFileRoute("/agents/automations/")({
  component: AutomationsIndexPage,
})

function AutomationsIndexPage() {
  return <AutomationsList />
}
