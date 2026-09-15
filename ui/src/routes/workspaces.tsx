import { createFileRoute } from "@tanstack/react-router"

import { AppShell } from "@/components/AppShell"
import { Skeleton } from "@/components/ui/skeleton"
import { WorkspacesSection } from "@/features/settings/components/WorkspacesSection"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/workspaces")({
  component: WorkspacesPage,
})

function WorkspacesPage() {
  const session = useSession()

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-40 w-full" />
      </main>
    )
  }
  if (!session.data) return <RequireLogin />

  return (
    <AppShell
      user={session.data}
      title="Workspaces"
      description="The sandbox images agent runs boot from, and how their nightly rebuilds went."
    >
      <WorkspacesSection isAdmin={session.data.is_admin} />
    </AppShell>
  )
}
