import { createFileRoute } from "@tanstack/react-router"
import { Conversation } from "@/features/assistant/Conversation"

export const Route = createFileRoute("/assistant/")({
  validateSearch: (search: Record<string, unknown>): { repo?: string } =>
    typeof search.repo === "string" && search.repo.trim()
      ? { repo: search.repo.trim() }
      : {},
  component: AssistantHome,
})

function AssistantHome() {
  const { repo } = Route.useSearch()
  return <Conversation initialRepo={repo} />
}
