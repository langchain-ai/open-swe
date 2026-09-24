import { createFileRoute } from "@tanstack/react-router"
import { IncidentDetail } from "@/features/incidents/IncidentDetail"

export const Route = createFileRoute("/incidents/$incidentId")({
  component: IncidentPage,
  head: ({ params }: { params: { incidentId: string } }) => ({
    meta: [{ title: `Incident ${params.incidentId} - Open SWE` }],
  }),
})

function IncidentPage() {
  const { incidentId } = Route.useParams()
  return <IncidentDetail key={incidentId} incidentId={incidentId} />
}
