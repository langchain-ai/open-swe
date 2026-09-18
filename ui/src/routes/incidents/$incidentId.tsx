import { createFileRoute } from "@tanstack/react-router"
import { IncidentDetail } from "@/features/incidents/IncidentDetail"

export const Route = createFileRoute("/incidents/$incidentId")({
  component: IncidentPage,
})

function IncidentPage() {
  const { incidentId } = Route.useParams()
  return <IncidentDetail key={incidentId} incidentId={incidentId} />
}
