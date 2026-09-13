import { Link, useRouterState } from "@tanstack/react-router"
import { GitPullRequest, MessagesSquare, Radar, Settings2 } from "lucide-react"
import type { ReactNode } from "react"

import { SidebarUserMenu } from "@/components/SidebarUserMenu"
import {
  SidebarCollapseButton,
  SidebarFrame,
  SidebarLayoutProvider,
  useSidebarLayout,
} from "@/components/sidebar-layout"
import type { SessionUser } from "@/lib/api"
import { cn } from "@/lib/utils"
import { IncidentsMark } from "./shared"

const navigation = [
  { to: "/agents", label: "Open SWE Agent", icon: MessagesSquare },
  { to: "/agents/reviews", label: "Reviews", icon: GitPullRequest },
  { to: "/incidents", label: "Incidents", icon: Radar },
] as const

export function IncidentsShell({
  user,
  children,
}: {
  user: SessionUser
  children: ReactNode
}) {
  const layout = useSidebarLayout()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const view = useRouterState({
    select: (state) =>
      (state.location.search as { view?: string }).view ?? "active",
  })
  const isList = pathname === "/incidents" || pathname === "/incidents/"
  return (
    <SidebarLayoutProvider value={layout}>
      <div className="agents-ui flex h-svh overflow-hidden bg-background">
        <SidebarFrame {...layout} className="border-r border-border bg-sidebar">
          <div className="flex items-center gap-2.5 px-4 py-5">
            <IncidentsMark />
            <Link to="/incidents" className="flex-1">
              <span className="block text-sm font-semibold tracking-tight">
                Incidents
              </span>
              <span className="text-[10px] text-muted-foreground">
                by Open SWE
              </span>
            </Link>
            <SidebarCollapseButton onToggle={layout.toggle} />
          </div>
          <nav className="space-y-1 px-2" aria-label="Open SWE navigation">
            {navigation.map(({ to, label, icon: Icon }) => {
              const selected = to === "/incidents"
              return (
                <Link
                  key={to}
                  to={to}
                  onClick={layout.closeOnMobile}
                  aria-current={selected ? "page" : undefined}
                  className={cn(
                    "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors hover:bg-sidebar-row-hover",
                    selected
                      ? "bg-sidebar-row-selected font-medium text-foreground"
                      : "text-muted-foreground"
                  )}
                >
                  <Icon className="size-4" />
                  {label}
                </Link>
              )
            })}
          </nav>
          {user.is_admin && (
            <div className="mt-6 px-2">
              <Link
                to="/admin"
                hash="incidents"
                onClick={layout.closeOnMobile}
                activeProps={{
                  className:
                    "bg-sidebar-row-selected text-foreground font-medium",
                }}
                className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-sidebar-row-hover"
              >
                <Settings2 className="size-4" />
                Admin settings
              </Link>
            </div>
          )}
          <div className="mt-auto p-4">
            <SidebarUserMenu user={user} showSettingsLink />
          </div>
        </SidebarFrame>
        <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <div
            className={cn(
              "flex h-12 shrink-0 items-center gap-2 border-b border-border px-5 text-xs text-muted-foreground",
              layout.collapsed && "pl-12"
            )}
          >
            <span className="font-medium text-foreground">Incidents</span>
            <span>/</span>
            <span>
              {pathname.endsWith("/settings")
                ? "Settings"
                : isList
                  ? view === "history"
                    ? "History"
                    : "All investigations"
                  : "Incident details"}
            </span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        </main>
      </div>
    </SidebarLayoutProvider>
  )
}
