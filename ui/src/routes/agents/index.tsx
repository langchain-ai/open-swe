import { createFileRoute } from "@tanstack/react-router"

import { AgentsHome } from "@/features/agents/components/AgentsHome"

interface AgentsIndexSearch {
  repo?: string
  localProject?: string
  noProject?: boolean
}

export const Route = createFileRoute("/agents/")({
  validateSearch: (search: Record<string, unknown>): AgentsIndexSearch => ({
    ...(typeof search.repo === "string" && search.repo.trim()
      ? { repo: search.repo.trim() }
      : {}),
    ...(typeof search.localProject === "string" && search.localProject.trim()
      ? { localProject: search.localProject.trim() }
      : {}),
    ...(search.noProject === true || search.noProject === "true"
      ? { noProject: true }
      : {}),
  }),
  component: AgentsIndexPage,
})

function AgentsIndexPage() {
  const { repo, localProject, noProject } = Route.useSearch()
  return (
    <AgentsHome
      key={`${repo ?? ""}:${localProject ?? ""}:${noProject ?? ""}`}
      initialRepo={repo}
      initialLocalProject={localProject}
      initialNoProject={noProject}
    />
  )
}
