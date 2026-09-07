import { Link, useRouterState } from "@tanstack/react-router"
import {
  Activity,
  ArrowLeft,
  CheckCheck,
  CircleAlert,
  Pause,
  Settings2,
} from "lucide-react"
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
import { InvestigateMark, investigationViews } from "./shared"

const viewIcons = {
  active: Activity,
  paused: Pause,
  needs_attention: CircleAlert,
  completed: CheckCheck,
}

export function InvestigateShell({
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
  const isList = pathname === "/investigate" || pathname === "/investigate/"
  return (
    <SidebarLayoutProvider value={layout}>
      <div className="agents-ui flex h-svh overflow-hidden bg-background">
        <SidebarFrame {...layout} className="border-r border-border bg-sidebar">
          <div className="flex items-center gap-2.5 px-4 py-5">
            <InvestigateMark />
            <Link to="/investigate" className="flex-1">
              <span className="block text-sm font-semibold tracking-tight">
                Investigate
              </span>
              <span className="text-[10px] text-muted-foreground">
                by Open SWE
              </span>
            </Link>
            <SidebarCollapseButton onToggle={layout.toggle} />
          </div>
          <div className="px-4 pt-5 pb-2 text-[10px] font-medium tracking-widest text-muted-foreground uppercase">
            Investigations
          </div>
          <nav className="space-y-1 px-2" aria-label="Investigate navigation">
            {investigationViews.map(({ value, label }) => {
              const Icon = viewIcons[value]
              return (
                <Link
                  key={value}
                  to="/investigate"
                  search={{ view: value }}
                  onClick={layout.closeOnMobile}
                  aria-current={isList && view === value ? "page" : undefined}
                  className={cn(
                    "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors hover:bg-sidebar-row-hover",
                    isList && view === value
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
                to="/investigate/settings"
                onClick={layout.closeOnMobile}
                activeProps={{
                  className:
                    "bg-sidebar-row-selected text-foreground font-medium",
                }}
                className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-sidebar-row-hover"
              >
                <Settings2 className="size-4" />
                Settings
              </Link>
            </div>
          )}
          <div className="mt-auto p-4">
            <Link
              to="/agents"
              className="mb-4 flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="size-3.5" />
              Open SWE Agent
            </Link>
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
            <span className="font-medium text-foreground">Investigate</span>
            <span>/</span>
            <span>
              {pathname.endsWith("/settings")
                ? "Settings"
                : isList
                  ? "Investigations"
                  : "Investigation details"}
            </span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        </main>
      </div>
    </SidebarLayoutProvider>
  )
}
