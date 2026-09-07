import { createFileRoute } from "@tanstack/react-router"
import { InvestigationSettings } from "@/features/investigate/InvestigationSettings"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/investigate/settings")({
  component: InvestigationSettingsPage,
})

function InvestigationSettingsPage() {
  const session = useSession()
  if (!session.data?.is_admin)
    return (
      <div role="alert" className="p-8 text-sm text-muted-foreground">
        Investigate settings are available to workspace administrators.
      </div>
    )
  return <InvestigationSettings />
}
