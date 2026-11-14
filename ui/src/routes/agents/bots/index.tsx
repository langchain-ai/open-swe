import { Navigate, createFileRoute } from "@tanstack/react-router"

// The Bots page was removed; old links land on the agents home instead of
// matching /agents/$threadId as a thread called "bots".
export const Route = createFileRoute("/agents/bots/")({
  component: () => <Navigate to="/agents" replace />,
})
