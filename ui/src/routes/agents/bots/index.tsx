import { createFileRoute } from "@tanstack/react-router"

import { BotThreadsList } from "@/features/bots/components/BotThreadsList"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/agents/bots/")({
  validateSearch: (search: Record<string, unknown>): { bot?: string } =>
    typeof search.bot === "string" && search.bot ? { bot: search.bot } : {},
  component: BotsIndexPage,
  head: () => ({ meta: [{ title: pageTitle("Bots") }] }),
})

function BotsIndexPage() {
  const { bot } = Route.useSearch()
  const navigate = Route.useNavigate()
  return (
    <BotThreadsList
      bot={bot}
      onBotChange={(next) => navigate({ search: next ? { bot: next } : {} })}
    />
  )
}
