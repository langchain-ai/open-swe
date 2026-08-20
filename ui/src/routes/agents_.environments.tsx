import { Navigate, createFileRoute } from "@tanstack/react-router"

import { AppShell } from "@/components/AppShell"
import { EnvironmentsPanel } from "@/components/EnvironmentsPanel"
import { RequireLogin } from "@/lib/auth-redirect"
import { Skeleton } from "@/components/ui/skeleton"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/agents_/environments")({
  component: EnvironmentsPage,
})

function EnvironmentsPage() {
  const session = useSession()

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    )
  }
  if (!session.data) return <RequireLogin />
  if (!session.data.is_admin) return <Navigate to="/my-settings" />

  return (
    <AppShell
      user={session.data}
      title="Environments"
      description="A named prompt plus a sandbox snapshot every run boots from. Snapshots are captured from an admin thread's sandbox once it is provisioned."
      backTo={{ to: "/cloud-agents", label: "Back to Open SWE Agent" }}
    >
      <div className="rounded-lg border border-border bg-card">
        <EnvironmentsPanel />
      </div>
    </AppShell>
  )
}
