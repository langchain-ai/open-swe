import { createFileRoute } from "@tanstack/react-router"

import { PlanView } from "@/features/agents/components/PlanView"

export const Route = createFileRoute("/agents/$threadId_/plan")({
  component: PlanPage,
  head: () => ({ meta: [{ title: "Artifact - Open SWE" }] }),
})

function PlanPage() {
  const { threadId } = Route.useParams()
  return <PlanView threadId={threadId} standalone />
}
