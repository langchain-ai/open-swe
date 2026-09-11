import { createFileRoute } from "@tanstack/react-router"
import { IncidentSettings } from "@/features/incidents/IncidentSettings"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/incidents/settings")({
  component: IncidentSettingsPage,
})

function IncidentSettingsPage() {
  const session = useSession()
  if (!session.data?.is_admin)
    return (
      <div role="alert" className="p-8 text-sm text-muted-foreground">
        Incidents settings are available to workspace administrators.
      </div>
    )
  return <IncidentSettings />
}
