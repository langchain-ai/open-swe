import { Navigate, createFileRoute } from "@tanstack/react-router"

export const Route = createFileRoute("/assistant/$threadId")({
  component: AssistantThreadRedirect,
})

function AssistantThreadRedirect() {
  const { threadId } = Route.useParams()
  return <Navigate to="/agents/$threadId" params={{ threadId }} replace />
}
