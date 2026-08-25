import { createFileRoute } from "@tanstack/react-router"

import { AgentsHome } from "@/features/agents/components/AgentsHome"

export const Route = createFileRoute("/agents/new")({
  component: AgentsNewPage,
})

function AgentsNewPage() {
  return <AgentsHome />
}
