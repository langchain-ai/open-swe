import { Navigate, createFileRoute } from "@tanstack/react-router"

export const Route = createFileRoute("/review_/styles")({
  component: () => (
    <Navigate to="/review/settings" search={{ tab: "instructions" }} replace />
  ),
})
