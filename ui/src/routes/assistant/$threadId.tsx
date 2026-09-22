import { createFileRoute } from "@tanstack/react-router"
import { Conversation } from "@/features/assistant/Conversation"

export const Route = createFileRoute("/assistant/$threadId")({
  component: Conversation,
})
