import { createFileRoute } from "@tanstack/react-router"
import { FleetDesktop } from "@/features/agents/components/FleetDesktop"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/agents/chat")({
  validateSearch: (
    search: Record<string, unknown>
  ): { workspace?: string; chat?: string } => ({
    workspace:
      typeof search.workspace === "string" ? search.workspace : undefined,
    chat: typeof search.chat === "string" ? search.chat : undefined,
  }),
  component: ChatPage,
  head: () => ({ meta: [{ title: pageTitle("Chat") }] }),
})

function ChatPage() {
  const search = Route.useSearch()
  const navigate = Route.useNavigate()
  return (
    <FleetDesktop
      key={search.workspace}
      workspaceId={search.workspace}
      threadId={search.chat}
      onSelect={(workspace, chat) => {
        void navigate({ search: { workspace, chat } })
      }}
    />
  )
}
