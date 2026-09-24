import { createFileRoute } from "@tanstack/react-router"
import { Conversation } from "@/features/assistant/Conversation"

export const Route = createFileRoute("/assistant/")({
  validateSearch: (
    search: Record<string, unknown>
  ): { repo?: string; noRepo?: boolean } => ({
    ...(typeof search.repo === "string" && search.repo.trim()
      ? { repo: search.repo.trim() }
      : {}),
    ...(search.noRepo === true || search.noRepo === "true"
      ? { noRepo: true }
      : {}),
  }),
  component: AssistantHome,
})

function AssistantHome() {
  const { repo, noRepo } = Route.useSearch()
  return <Conversation initialRepo={noRepo ? null : repo} />
}
