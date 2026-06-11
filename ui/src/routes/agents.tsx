import {
  Navigate,
  Outlet,
  createFileRoute,
  useRouterState,
} from "@tanstack/react-router"

import { AgentsShell } from "@/components/agents/AgentsSidebar"
import { Skeleton } from "@/components/ui/skeleton"
import agentsCss from "@/styles/agents.css?url"
import { AgentThreadStreamProvider } from "@/lib/agents/AgentThreadStreamProvider"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/agents")({
  head: () => ({
    links: [{ rel: "stylesheet", href: agentsCss }],
  }),
  component: AgentsLayout,
})

function AgentsLayout() {
  const session = useSession()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const [, section, threadId] = pathname.split("/")
  const activeThreadId =
    section === "agents" && threadId && threadId !== "automations"
      ? threadId
      : undefined

  if (session.isLoading) {
    return (
      <main className="agents-ui flex h-svh items-center justify-center bg-[var(--ui-bg)] p-6">
        <Skeleton className="h-40 w-full max-w-md" />
      </main>
    )
  }

  if (!session.data) return <Navigate to="/login" />

  return (
    <AgentsShell user={session.data} activeThreadId={activeThreadId}>
      <AgentThreadStreamProvider threadId={activeThreadId ?? null}>
        <Outlet />
      </AgentThreadStreamProvider>
    </AgentsShell>
  )
}
