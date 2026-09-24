import { createFileRoute } from "@tanstack/react-router"
import { IncidentDetail } from "@/features/incidents/IncidentDetail"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/incidents/$incidentId")({
  component: IncidentPage,
  head: ({ params }: { params: { incidentId: string } }) => ({
    meta: [{ title: pageTitle(`Incident ${params.incidentId}`) }],
  }),
})

function IncidentPage() {
  const { incidentId } = Route.useParams()
  return <IncidentDetail key={incidentId} incidentId={incidentId} />
}
