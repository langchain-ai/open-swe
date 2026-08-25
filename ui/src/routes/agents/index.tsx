import { createFileRoute } from "@tanstack/react-router"

import { SessionsHome } from "@/features/agents/components/SessionsHome"
import { useSession } from "@/lib/session"
import { isDesktopLocalModeEnabled } from "@/lib/desktop-local-mode"

export const Route = createFileRoute("/agents/")({
  component: AgentsIndexPage,
})

function AgentsIndexPage() {
  const session = useSession()
  return (
    <SessionsHome
      user={session.data ?? null}
      localOnly={!session.data && isDesktopLocalModeEnabled()}
    />
  )
}
