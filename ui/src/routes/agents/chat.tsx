import { Navigate, createFileRoute } from "@tanstack/react-router"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/agents/chat")({
  validateSearch: (search: Record<string, unknown>): { chat?: string } => ({
    chat: typeof search.chat === "string" ? search.chat : undefined,
  }),
  component: ChatPage,
  head: () => ({ meta: [{ title: pageTitle("Chat") }] }),
})

function ChatPage() {
  const { chat } = Route.useSearch()
  return chat ? (
    <Navigate to="/agents/$threadId" params={{ threadId: chat }} replace />
  ) : (
    <Navigate to="/agents" replace />
  )
}
