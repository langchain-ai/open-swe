import { Navigate, createFileRoute } from "@tanstack/react-router"

import { AuthedAppShell } from "@/components/AppShell"
import { useWorkspaceOptions } from "@/features/agents/lib/queries"
import { WorkspaceSettingsPanel } from "@/features/settings/components/WorkspaceSettings"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/workspaces_/$slug")({
  component: WorkspaceSettingsPage,
})

function WorkspaceSettingsPage() {
  const { slug } = Route.useParams()
  const navigate = Route.useNavigate()
  const session = useSession()
  const options = useWorkspaceOptions(!!session.data)

  if (session.data && !session.data.is_admin)
    return <Navigate to="/workspaces" />

  const name =
    options.data?.workspaces.find((workspace) => workspace.slug === slug)
      ?.name ?? slug

  return (
    <AuthedAppShell
      title={name}
      description="Everything configured for this workspace: what it owns, the sandbox image its runs boot from, model defaults, review settings, and MCP connections."
      backTo={{ to: "/workspaces", label: "Back to Workspaces" }}
    >
      {() => (
        <WorkspaceSettingsPanel
          key={slug}
          slug={slug}
          canEdit
          onDeleted={() => void navigate({ to: "/workspaces" })}
        />
      )}
    </AuthedAppShell>
  )
}
