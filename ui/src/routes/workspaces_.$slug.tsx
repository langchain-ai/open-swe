import { Navigate, createFileRoute } from "@tanstack/react-router"

import { AppShell } from "@/components/AppShell"
import { Skeleton } from "@/components/ui/skeleton"
import { useWorkspaceOptions } from "@/features/agents/lib/queries"
import { WorkspaceSettings } from "@/features/settings/components/WorkspaceSettings"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/workspaces_/$slug")({
  component: WorkspaceSettingsPage,
})

function WorkspaceSettingsPage() {
  const { slug } = Route.useParams()
  const session = useSession()
  const options = useWorkspaceOptions(!!session.data)

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-40 w-full" />
      </main>
    )
  }
  if (!session.data) return <RequireLogin />
  if (!session.data.is_admin) return <Navigate to="/workspaces" />

  const name =
    options.data?.workspaces.find((workspace) => workspace.slug === slug)
      ?.name ?? slug

  return (
    <AppShell
      user={session.data}
      title={name}
      description="Everything configured for this workspace: what it owns, the sandbox image its runs boot from, model defaults, review settings, and MCP connections."
      backTo={{ to: "/workspaces", label: "Back to Workspaces" }}
    >
      <WorkspaceSettings slug={slug} canEdit />
    </AppShell>
  )
}
