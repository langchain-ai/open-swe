import { createFileRoute } from "@tanstack/react-router"

import { InboxPage } from "@/features/agents/components/InboxPage"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/agents/inbox")({
  component: InboxPage,
  head: () => ({ meta: [{ title: pageTitle("Inbox") }] }),
})
