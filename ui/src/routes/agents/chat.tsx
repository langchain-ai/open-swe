import { Navigate, createFileRoute } from "@tanstack/react-router"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/agents/chat")({
  component: ChatPage,
  head: () => ({ meta: [{ title: pageTitle("Chat") }] }),
})

function ChatPage() {
  return <Navigate to="/agents" search={{}} replace />
}
