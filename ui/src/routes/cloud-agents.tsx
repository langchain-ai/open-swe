import { Navigate, createFileRoute } from "@tanstack/react-router"

export const Route = createFileRoute("/cloud-agents")({
  component: () => <Navigate to="/my-settings/agent" replace />,
})
