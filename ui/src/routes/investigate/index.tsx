import { createFileRoute } from "@tanstack/react-router"
import { InvestigationList } from "@/features/investigate/InvestigationList"
import type { InvestigationView } from "@/features/investigate/api"

export const Route = createFileRoute("/investigate/")({
  validateSearch: (
    search: Record<string, unknown>
  ): { view?: InvestigationView } => ({
    view: ["active", "paused", "needs_attention", "completed"].includes(
      String(search.view)
    )
      ? (search.view as InvestigationView)
      : undefined,
  }),
  component: InvestigationsPage,
})

function InvestigationsPage() {
  const { view = "active" } = Route.useSearch()
  const navigate = Route.useNavigate()
  return (
    <InvestigationList
      view={view}
      onViewChange={(nextView) => {
        void navigate({ search: { view: nextView } })
      }}
    />
  )
}
