import { useEffect } from "react"
import {
  Navigate,
  Outlet,
  createFileRoute,
  useMatch,
  useRouterState,
} from "@tanstack/react-router"
import { Skeleton } from "@/components/ui/skeleton"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"
import { useExperimentalAssistantUi, useProfile } from "@/lib/profile"
import { rememberAppLocation } from "@/lib/appLocation"
import { AssistantProvider } from "@/features/assistant/AssistantProvider"
import { AgentsShell } from "@/features/agents/components/AgentsSidebar"

export const Route = createFileRoute("/assistant")({
  component: AssistantLayout,
})

function AssistantLayout() {
  const session = useSession()
  const profile = useProfile()
  const experimental = useExperimentalAssistantUi()
  const match = useMatch({ from: "/assistant/$threadId", shouldThrow: false })
  const threadId = match?.params.threadId
  const navigate = Route.useNavigate()
  const href = useRouterState({ select: (state) => state.location.href })
  useEffect(() => {
    document.documentElement.dataset["agentsTheme"] = "true"
    return () => {
      delete document.documentElement.dataset["agentsTheme"]
    }
  }, [])
  useEffect(() => rememberAppLocation(href), [href])
  if (session.isLoading || (session.data && profile.isPending))
    return (
      <main className="agents-ui flex h-svh items-center justify-center bg-background">
        <Skeleton className="h-40 w-full max-w-md" />
      </main>
    )
  if (!session.data) return <RequireLogin />
  if (!experimental)
    return threadId ? (
      <Navigate to="/agents/$threadId" params={{ threadId }} replace />
    ) : (
      <Navigate to="/agents" replace />
    )
  return (
    <AssistantProvider
      threadId={threadId}
      onThreadChange={(id) => {
        void (id
          ? navigate({ to: "/assistant/$threadId", params: { threadId: id } })
          : navigate({ to: "/assistant" }))
      }}
    >
      <AgentsShell user={session.data} activeThreadId={threadId}>
        <Outlet />
      </AgentsShell>
    </AssistantProvider>
  )
}
