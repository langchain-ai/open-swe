import { Navigate, createFileRoute } from "@tanstack/react-router"

export const Route = createFileRoute("/integrations")({
  component: () => <Navigate to="/my-settings/connections" replace />,
})
