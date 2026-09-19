import { Navigate, createFileRoute } from "@tanstack/react-router"

export const Route = createFileRoute("/review_/approval")({
  component: () => (
    <Navigate to="/review/settings" search={{ tab: "approval" }} replace />
  ),
})
