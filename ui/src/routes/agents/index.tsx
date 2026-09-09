import { createFileRoute } from "@tanstack/react-router"

import { AgentsHome } from "@/features/agents/components/AgentsHome"

interface AgentsIndexSearch {
  repo?: string
  localProject?: string
}

export const Route = createFileRoute("/agents/")({
  validateSearch: (search: Record<string, unknown>): AgentsIndexSearch => ({
    ...(typeof search.repo === "string" && search.repo.trim()
      ? { repo: search.repo.trim() }
      : {}),
    ...(typeof search.localProject === "string" && search.localProject.trim()
      ? { localProject: search.localProject.trim() }
      : {}),
  }),
  component: AgentsIndexPage,
})

function AgentsIndexPage() {
  const { repo, localProject } = Route.useSearch()
  return (
    <AgentsHome
      key={`${repo ?? ""}:${localProject ?? ""}`}
      initialRepo={repo}
      initialLocalProject={localProject}
    />
  )
}
