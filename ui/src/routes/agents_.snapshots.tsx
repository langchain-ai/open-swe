import { Navigate, createFileRoute } from "@tanstack/react-router"

import { AppShell } from "@/components/AppShell"
import { BaseSnapshotPanel } from "@/components/BaseSnapshotPanel"
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
      description="Control the sandbox images runs boot from. Anything without its own snapshot falls back to the default sandbox image."
      backTo={{ to: "/cloud-agents", label: "Back to Open SWE Agent" }}
    >
      <div className="flex flex-col gap-6">
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-foreground">
            Nightly base snapshot
          </h2>
          <p className="text-xs text-muted-foreground">
            Rebuild the shared sandbox snapshot on a schedule with the repos
            Open SWE works on most already cloned. Repos with their own snapshot
            below still take precedence.
          </p>
          <div className="rounded-lg border border-border bg-card">
            <BaseSnapshotPanel />
          </div>
        </section>

        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-foreground">
            Per-repository snapshots
          </h2>
          <p className="text-xs text-muted-foreground">
            Build a sandbox image for one repo from a custom Dockerfile, for
            repos needing toolchains the default image doesn't carry.
          </p>
          <div className="rounded-lg border border-border bg-card">
            <RepoSnapshotsPanel />
          </div>
        </section>
      </div>
    </AppShell>
  )
}
