import { createFileRoute } from "@tanstack/react-router"

import { AppShell } from "@/components/AppShell"
import { Skeleton } from "@/components/ui/skeleton"
import { EnvironmentsSection } from "@/features/settings/components/EnvironmentsSection"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/environments")({
  component: EnvironmentsPage,
})

function EnvironmentsPage() {
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
      title="Environments"
      description="The sandbox images agent runs boot from, and how their nightly rebuilds went."
    >
      <EnvironmentsSection isAdmin={session.data.is_admin} />
    </AppShell>
  )
}
