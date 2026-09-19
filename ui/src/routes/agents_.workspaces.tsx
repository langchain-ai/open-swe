import { Navigate, createFileRoute } from "@tanstack/react-router"

export const Route = createFileRoute("/agents_/workspaces")({
  component: WorkspacesRedirect,
})

function WorkspacesRedirect() {
  return <Navigate to="/workspaces" />
}
