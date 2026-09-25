import { createFileRoute } from "@tanstack/react-router"

import { PlanView } from "@/features/agents/components/PlanView"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/agents/$threadId_/plan")({
  component: PlanPage,
  head: () => ({ meta: [{ title: pageTitle("Artifact") }] }),
})

function PlanPage() {
  const { threadId } = Route.useParams()
  return <PlanView threadId={threadId} standalone />
}
