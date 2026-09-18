import { createFileRoute } from "@tanstack/react-router"

import { AgentsHome } from "@/features/agents/components/AgentsHome"

interface AgentsIndexSearch {
  repo?: string
  localRepo?: string
  noRepo?: boolean
}

export const Route = createFileRoute("/agents/")({
  validateSearch: (search: Record<string, unknown>): AgentsIndexSearch => ({
    ...(typeof search.repo === "string" && search.repo.trim()
      ? { repo: search.repo.trim() }
      : {}),
    ...(typeof search.localRepo === "string" && search.localRepo.trim()
      ? { localRepo: search.localRepo.trim() }
      : {}),
    ...(search.noRepo === true || search.noRepo === "true"
      ? { noRepo: true }
      : {}),
  }),
  component: AgentsIndexPage,
})

function AgentsIndexPage() {
  const { repo, localRepo, noRepo } = Route.useSearch()
  return (
    <AgentsHome
      key={`${repo ?? ""}:${localRepo ?? ""}:${noRepo ?? ""}`}
      initialRepo={repo}
      initialLocalRepo={localRepo}
      initialNoRepo={noRepo}
    />
  )
}
