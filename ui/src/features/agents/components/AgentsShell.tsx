import { useEffect, useMemo } from "react"
import { useNavigate, useRouterState } from "@tanstack/react-router"

import type { SessionUser } from "@/lib/api"
import { TabBar } from "@/features/agents/components/TabBar"
import { useTabActivity } from "@/features/agents/lib/tabActivity"
import {
  HOME_PATH,
  NEW_TAB_PATH,
  activateTab,
  closeTab,
  tabForPathname,
  openTab,
  updateTabs,
  useAgentTabs,
} from "@/features/agents/lib/tabs"
import { useRegisterAppCommands } from "@/lib/appCommands"

/**
 * The tabbed frame every agents route renders inside: a strip of open sessions
 * across the top, the route below it. The router stays the source of truth —
 * this only mirrors the current path into the tab store so the strip has
 * something to show, and reopens a tab for any path the user lands on directly.
 */
export function AgentsShell({
  user,
  localOnly = false,
  activeThreadId,
  children,
}: {
  user: SessionUser | null
  localOnly?: boolean
  activeThreadId?: string
  children: React.ReactNode
}) {
  const navigate = useNavigate()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const { tabs, activeId } = useAgentTabs()
  const activity = useTabActivity({
    activeThreadId,
    cloudEnabled: Boolean(user) && !localOnly,
  })

  useEffect(() => {
    const tab = tabForPathname(pathname)
    updateTabs((current) =>
      tab ? openTab(current, tab) : activateTab(current, null)
    )
  }, [pathname])

  const commands = useMemo(
    () => [
      {
        id: "all-sessions",
        label: "All sessions",
        aliases: ["home", "sessions"],
        group: "Navigation",
        run: () => void navigate({ to: HOME_PATH }),
      },
      {
        id: "new-session-tab",
        label: "New session tab",
        aliases: ["new tab"],
        group: "Navigation",
        run: () => void navigate({ to: NEW_TAB_PATH }),
      },
      {
        id: "close-tab",
        label: "Close tab",
        group: "Navigation",
        available: activeId !== null,
        run: () => {
          if (!activeId) return
          const next = updateTabs((current) => closeTab(current, activeId))
          const target = next.tabs.find((tab) => tab.id === next.activeId)
          void navigate({ to: target?.path ?? HOME_PATH })
        },
      },
      ...tabs.map((tab, index) => ({
        id: `switch-tab-${tab.id}`,
        label: `Switch to ${tab.title}`,
        aliases: [`tab ${index + 1}`],
        group: "Tabs",
        run: () => void navigate({ to: tab.path }),
      })),
    ],
    [activeId, navigate, tabs]
  )
  useRegisterAppCommands(commands)

  return (
    <div className="agents-ui flex h-svh flex-col overflow-hidden bg-background">
      <TabBar activity={activity} />
      <main className="surface-grain relative flex min-h-0 min-w-0 flex-1 overflow-hidden bg-background">
        {children}
      </main>
    </div>
  )
}
