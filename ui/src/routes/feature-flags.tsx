import { Navigate, createFileRoute } from "@tanstack/react-router"

export const Route = createFileRoute("/feature-flags")({
  component: () => <Navigate to="/my-settings/experiments" replace />,
})
