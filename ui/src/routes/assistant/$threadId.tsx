import { createFileRoute } from "@tanstack/react-router"
import { Conversation } from "@/features/assistant/Conversation"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/assistant/$threadId")({
  component: Conversation,
  head: () => ({ meta: [{ title: pageTitle("Assistant") }] }),
})
