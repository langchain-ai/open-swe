import { createFileRoute } from "@tanstack/react-router"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/copilot/$threadId")({
  component: () => null,
  head: () => ({ meta: [{ title: pageTitle("CopilotKit") }] }),
})
