import { Navigate, createFileRoute } from "@tanstack/react-router"

export const Route = createFileRoute("/agents_/workspaces")({
  component: WorkspacesPage,
})

function WorkspacesPage() {
  return <Navigate to="/workspaces" />
}
