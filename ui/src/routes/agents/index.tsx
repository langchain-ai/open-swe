import { createFileRoute } from "@tanstack/react-router"

import { AgentsHome } from "@/features/agents/components/AgentsHome"

export const Route = createFileRoute("/agents/")({
  component: AgentsIndexPage,
})

function AgentsIndexPage() {
  return <AgentsHome />
}
