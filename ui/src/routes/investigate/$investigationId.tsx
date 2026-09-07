import { createFileRoute } from "@tanstack/react-router"
import { InvestigationDetail } from "@/features/investigate/InvestigationDetail"

export const Route = createFileRoute("/investigate/$investigationId")({
  component: InvestigationPage,
})

function InvestigationPage() {
  const { investigationId } = Route.useParams()
  return (
    <InvestigationDetail
      key={investigationId}
      investigationId={investigationId}
    />
  )
}
