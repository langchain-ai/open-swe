import { Navigate, createFileRoute } from "@tanstack/react-router"

export const Route = createFileRoute("/my-settings_/instructions")({
  component: () => (
    <Navigate to="/my-settings/agent" hash="instructions" replace />
  ),
})
