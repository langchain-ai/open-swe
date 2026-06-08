import { createFileRoute } from "@tanstack/react-router"

import { AgentsHome } from "@/components/agents/AgentsHome"

export const Route = createFileRoute("/agents/")({
  component: AgentsIndexPage,
})

function AgentsIndexPage() {
  return <AgentsHome />
}
