import { createFileRoute } from "@tanstack/react-router"

import { AutomationsList } from "@/features/automations/components/AutomationsList"

export const Route = createFileRoute("/agents/automations/")({
  component: AutomationsIndexPage,
})

function AutomationsIndexPage() {
  return <AutomationsList />
}
