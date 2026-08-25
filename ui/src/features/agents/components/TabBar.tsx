import { Link, useNavigate, useRouterState } from "@tanstack/react-router"
import {
  CircleNotchIcon,
  CloudIcon,
  DesktopIcon,
  NotePencilIcon,
  PlusIcon,
  SidebarSimpleIcon,
  SquaresFourIcon,
  XIcon,
} from "@phosphor-icons/react"
import type { ComponentType, SVGProps } from "react"

import type { AgentTab, AgentTabKind } from "@/features/agents/lib/tabs"
import type { TabActivity } from "@/features/agents/lib/tabActivity"
import {
  HOME_PATH,
  NEW_TAB_PATH,
  activateTab,
  closeTab,
  updateTabs,
  useAgentTabs,
} from "@/features/agents/lib/tabs"
import { useRightPanelToggle } from "@/features/agents/lib/rightPanelToggle"
import { cn } from "@/lib/utils"

const KIND_ICON: Record<
  AgentTabKind,
  { icon: ComponentType<SVGProps<SVGSVGElement>>; label: string }
> = {
  cloud: { icon: CloudIcon, label: "Cloud session" },
  local: { icon: DesktopIcon, label: "Session on this computer" },
  new: { icon: NotePencilIcon, label: "New session" },
}

export function TabBar({
  activity = {},
}: {
  activity?: Record<string, TabActivity>
}) {
  const { tabs, activeId } = useAgentTabs()
  const navigate = useNavigate()
  const isHome = useRouterState({
    select: (state) =>
      state.location.pathname.replace(/\/+$/, "") === HOME_PATH,
  })
  const rightPanel = useRightPanelToggle()
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)

  const close = (tab: AgentTab) => {
    const wasActive = activeId === tab.id
    const next = updateTabs((current) => closeTab(current, tab.id))
    if (!wasActive) return
    const target = next.tabs.find((item) => item.id === next.activeId)
    void navigate({ to: target?.path ?? HOME_PATH })
  }

  return (
    <div
      data-desktop-top-strip=""
      className={cn(
        "flex h-11 shrink-0 items-center gap-1 bg-background px-2",
        // Clears the traffic lights; the preload stylesheet undoes it in fullscreen.
        isDesktop && "pl-[88px]"
      )}
    >
      <Link
        to={HOME_PATH}
        aria-label="All sessions"
        title="All sessions"
        onClick={() => updateTabs((current) => activateTab(current, null))}
        className={cn(
          "flex size-7 shrink-0 items-center justify-center rounded-md transition-colors",
          isHome
            ? "bg-sidebar-row-hover text-foreground"
            : "text-muted-foreground hover:bg-sidebar-row-hover hover:text-foreground"
        )}
      >
        <SquaresFourIcon className="size-4" />
      </Link>

      <div className="flex min-w-0 flex-1 scrollbar-none items-center gap-1 overflow-x-auto">
        <div
          role="tablist"
          aria-label="Open sessions"
          className="flex items-center gap-1"
        >
          {tabs.map((tab) => (
            <TabItem
              key={tab.id}
              tab={tab}
              isActive={tab.id === activeId}
              activity={activity[tab.id]}
              onClose={() => close(tab)}
            />
          ))}
        </div>
        <Link
          to={NEW_TAB_PATH}
          aria-label="New session"
          title="New session"
          className="flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-row-hover hover:text-foreground"
        >
          <PlusIcon className="size-4" />
        </Link>
      </div>

      {rightPanel && (
        <button
          type="button"
          onClick={rightPanel.toggle}
          aria-label={rightPanel.collapsed ? "Show panel" : "Hide panel"}
          title={rightPanel.collapsed ? "Show panel" : "Hide panel"}
          aria-pressed={!rightPanel.collapsed}
          className="flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-row-hover hover:text-foreground"
        >
          <SidebarSimpleIcon className="size-4" />
        </button>
      )}
    </div>
  )
}

function TabItem({
  tab,
  isActive,
  activity,
  onClose,
}: {
  tab: AgentTab
  isActive: boolean
  activity?: TabActivity
  onClose: () => void
}) {
  const { icon: Icon, label } = KIND_ICON[tab.kind]

  return (
    <div
      className={cn(
        "group flex h-8 w-44 shrink-0 items-center gap-1.5 rounded-md pr-1 pl-2 transition-colors",
        isActive
          ? "bg-sidebar-row-hover text-foreground"
          : "text-muted-foreground hover:bg-sidebar-row-hover"
      )}
    >
      <Link
        to={tab.path}
        role="tab"
        aria-selected={isActive}
        title={tab.title}
        onAuxClick={(event: React.MouseEvent) => {
          if (event.button !== 1) return
          event.preventDefault()
          onClose()
        }}
        className="flex min-w-0 flex-1 items-center gap-1.5"
      >
        {activity === "running" ? (
          <CircleNotchIcon
            className="size-3.5 shrink-0 animate-spin text-primary"
            aria-label="Running"
          />
        ) : (
          <Icon className="size-3.5 shrink-0 text-muted-foreground/80">
            <title>{label}</title>
          </Icon>
        )}
        <span className="min-w-0 flex-1 truncate text-left text-[13px]">
          {tab.title}
        </span>
        {activity === "attention" && (
          <span
            className="size-1.5 shrink-0 rounded-full bg-primary"
            aria-label="Finished"
          />
        )}
      </Link>
      <button
        type="button"
        aria-label={`Close ${tab.title}`}
        onClick={onClose}
        className="flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground/70 opacity-0 transition-opacity group-hover:opacity-100 hover:bg-accent hover:text-foreground focus-visible:opacity-100 [@media(hover:none)]:opacity-100"
      >
        <XIcon className="size-3" />
      </button>
    </div>
  )
}
