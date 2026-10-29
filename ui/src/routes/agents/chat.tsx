import { useCallback } from "react"
import { createFileRoute } from "@tanstack/react-router"
import { FleetHome } from "@/features/agents/components/FleetHome"
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
  const navigate = Route.useNavigate()
  const select = useCallback(
    (thread: string) => void navigate({ search: { chat: thread } }),
    [navigate]
  )
  return <FleetHome threadId={chat} onThreadCreated={select} />
}
