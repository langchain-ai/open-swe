import { createFileRoute, Navigate } from "@tanstack/react-router"

export const Route = createFileRoute("/docs")({
  component: () => <Navigate to="/review" replace />,
})
