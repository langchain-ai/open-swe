import { useEffect } from "react"

import { createFileRoute } from "@tanstack/react-router"

import { AgentsHome } from "@/features/agents/components/AgentsHome"

interface AgentsIndexSearch {
  prompt?: string
  repo?: string
  localRepo?: string
  noRepo?: boolean
  project?: string
}

export const Route = createFileRoute("/agents/")({
  validateSearch: (search: Record<string, unknown>): AgentsIndexSearch => ({
    ...(typeof search.prompt === "string" && search.prompt.trim()
      ? { prompt: search.prompt }
      : {}),
    ...(typeof search.repo === "string" && search.repo.trim()
      ? { repo: search.repo.trim() }
      : {}),
    ...(typeof search.localRepo === "string" && search.localRepo.trim()
      ? { localRepo: search.localRepo.trim() }
      : {}),
    ...(search.noRepo === true || search.noRepo === "true"
      ? { noRepo: true }
      : {}),
    ...(typeof search.project === "string" && search.project.trim()
      ? { project: search.project.trim() }
      : {}),
  }),
  component: AgentsIndexPage,
})

function AgentsIndexPage() {
  const { prompt, repo, localRepo, noRepo, project } = Route.useSearch()
  const navigate = Route.useNavigate()
  useEffect(() => {
    if (!prompt) return
    void navigate({
      search: (previous) => ({ ...previous, prompt: undefined }),
      replace: true,
    })
  }, [navigate, prompt])
  return (
    <AgentsHome
      key={`${repo ?? ""}:${localRepo ?? ""}:${noRepo ?? ""}:${project ?? ""}`}
      initialPrompt={prompt}
      initialProject={project}
      initialRepo={repo}
      initialLocalRepo={localRepo}
      initialNoRepo={noRepo}
    />
  )
}
