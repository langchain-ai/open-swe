import { useState } from "react"
import { createFileRoute } from "@tanstack/react-router"

import { AgentsHome } from "@/features/agents/components/AgentsHome"
import { consumeWorkspaceRefreshFix } from "@/features/agents/lib/workspaceRefreshFix"

interface AgentsIndexSearch {
  repo?: string
  localProject?: string
  noProject?: boolean
  fix?: string
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
    ...(typeof search.fix === "string" && search.fix.trim()
      ? { fix: search.fix.trim() }
      : {}),
  }),
  component: AgentsIndexPage,
})

function AgentsIndexPage() {
  const { repo, localProject, noProject, fix } = Route.useSearch()
  const [stagedFix] = useState(() => consumeWorkspaceRefreshFix(fix))
  return (
    <AgentsHome
      key={`${repo ?? ""}:${localProject ?? ""}:${noProject ?? ""}:${fix ?? ""}`}
      initialRepo={repo}
      initialLocalProject={localProject}
      initialNoProject={noProject}
      initialPrompt={stagedFix?.prompt}
      initialWorkspace={stagedFix?.workspace}
      initialVisibility={stagedFix ? "private" : undefined}
    />
  )
}
