import { Navigate, createFileRoute } from "@tanstack/react-router"

import { AppShell } from "@/components/AppShell"
import { SandboxSettingsPanel } from "@/components/SandboxSettingsPanel"
import { RequireLogin } from "@/lib/auth-redirect"
import { Skeleton } from "@/components/ui/skeleton"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/agents_/sandbox")({
  component: SandboxSettingsPage,
})

function SandboxSettingsPage() {
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
      title="Sandbox"
      description="The snapshot new sandboxes boot from when their environment has none."
      backTo={{ to: "/cloud-agents", label: "Back to Open SWE Agent" }}
    >
      <div className="rounded-lg border border-border bg-card">
        <SandboxSettingsPanel />
      </div>
    </AppShell>
  )
}
