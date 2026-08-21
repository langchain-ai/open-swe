import { Navigate, createFileRoute } from "@tanstack/react-router"

export const Route = createFileRoute("/agents_/environments")({
  component: EnvironmentsPage,
})

function EnvironmentsPage() {
  return <Navigate to="/my-settings" />
}
