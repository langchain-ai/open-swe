import { createFileRoute } from "@tanstack/react-router"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/copilot/")({
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
  component: () => null,
  head: () => ({ meta: [{ title: pageTitle("CopilotKit") }] }),
})
