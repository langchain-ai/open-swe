import { Navigate, createFileRoute } from "@tanstack/react-router"

export const Route = createFileRoute("/my-settings_/experiments")({
  component: () => <Navigate to="/my-settings/feature-flags" replace />,
})
