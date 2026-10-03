import { useEffect, useState } from "react"
import {
  Navigate,
  createFileRoute,
  useMatch,
  useRouterState,
} from "@tanstack/react-router"
import copilotKitCss from "@copilotkit/react-core/v2/styles.css?url"
import { Skeleton } from "@/components/ui/skeleton"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"
import { useExperimentalCopilotKitUi, useProfile } from "@/lib/profile"
import { rememberAppLocation } from "@/lib/appLocation"
import { AgentsShell } from "@/features/agents/components/AgentsSidebar"
import { CopilotProvider } from "@/features/copilot/CopilotProvider"
import { Conversation } from "@/features/copilot/Conversation"

export const Route = createFileRoute("/copilot")({
  component: CopilotLayout,
  head: () => ({ links: [{ rel: "stylesheet", href: copilotKitCss }] }),
})

function CopilotLayout() {
  const session = useSession()
  const profile = useProfile()
  const enabled = useExperimentalCopilotKitUi()
  const threadMatch = useMatch({
    from: "/copilot/$threadId",
    shouldThrow: false,
  })
  const homeMatch = useMatch({ from: "/copilot/", shouldThrow: false })
  const routeThreadId = threadMatch?.params.threadId
  const location = useRouterState({ select: (state) => state.location })
  // The home route drafts a thread under a fresh id. The draft keeps that id when
  // its first run gives it a URL, and is replaced only after it has been used.
  const [draft, setDraft] = useState(() => ({
    threadId: crypto.randomUUID(),
    used: false,
  }))
  if (routeThreadId === draft.threadId && !draft.used)
    setDraft({ ...draft, used: true })
  if (homeMatch && draft.used)
    setDraft({ threadId: crypto.randomUUID(), used: false })
  const draftThreadId = draft.threadId
  useEffect(() => {
    document.documentElement.dataset["agentsTheme"] = "true"
    return () => {
      delete document.documentElement.dataset["agentsTheme"]
    }
  }, [])
  useEffect(() => rememberAppLocation(location.href), [location.href])

  if (session.isLoading || (session.data && profile.isPending))
    return (
      <main className="agents-ui flex h-svh items-center justify-center bg-background">
        <Skeleton className="h-40 w-full max-w-md" />
      </main>
    )
  if (!session.data) return <RequireLogin />
  if (!enabled)
    return routeThreadId ? (
      <Navigate
        to="/agents/$threadId"
        params={{ threadId: routeThreadId }}
        replace
      />
    ) : (
      <Navigate to="/agents" replace />
    )
  const threadId = routeThreadId ?? draftThreadId
  const search = homeMatch?.search
  return (
    <CopilotProvider>
      <AgentsShell user={session.data} activeThreadId={routeThreadId}>
        <Conversation
          key={threadId}
          threadId={threadId}
          isNew={!routeThreadId}
          initialRepo={search?.noRepo ? null : search?.repo}
        />
      </AgentsShell>
    </CopilotProvider>
  )
}
