import { Navigate, createFileRoute } from "@tanstack/react-router"

export const Route = createFileRoute("/agents/skills")({
  component: () => <Navigate to="/skills" replace />,
})
