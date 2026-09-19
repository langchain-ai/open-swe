import { createFileRoute } from "@tanstack/react-router"
import { Conversation } from "@/features/assistant/Conversation"

export const Route = createFileRoute("/assistant/")({
  validateSearch: (
    search: Record<string, unknown>
  ): { repo?: string; noProject?: boolean } => ({
    ...(typeof search.repo === "string" && search.repo.trim()
      ? { repo: search.repo.trim() }
      : {}),
    ...(search.noProject === true || search.noProject === "true"
      ? { noProject: true }
      : {}),
  }),
  component: AssistantHome,
})

function AssistantHome() {
  const { repo, noProject } = Route.useSearch()
  return <Conversation initialRepo={noProject ? null : repo} />
}
