import { createFileRoute } from "@tanstack/react-router"

import { QAAgentPage } from "@/features/reviews/frontend/QAAgentPage"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/agents/qa")({
  component: QAAgentPage,
  head: () => ({ meta: [{ title: pageTitle("QA agent") }] }),
})
