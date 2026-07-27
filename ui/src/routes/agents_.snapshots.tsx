import { Navigate, createFileRoute } from "@tanstack/react-router"

import { AppShell } from "@/components/AppShell"
import { RepoSnapshotsPanel } from "@/components/RepoSnapshotsPanel"
import { RequireLogin } from "@/lib/auth-redirect"
import { Skeleton } from "@/components/ui/skeleton"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/agents_/snapshots")({
  component: RepoSnapshotsPage,
})

function RepoSnapshotsPage() {
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
      title="Repository Snapshots"
      description="Build a per-repo sandbox image from a custom Dockerfile. Repos without a ready snapshot fall back to the default sandbox image."
      backTo={{ to: "/cloud-agents", label: "Back to Jarvis Agent" }}
    >
      <div className="rounded-lg border border-border bg-card">
        <RepoSnapshotsPanel />
      </div>
    </AppShell>
  )
}
