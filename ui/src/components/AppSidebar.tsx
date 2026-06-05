import { Link } from "@tanstack/react-router"
import {
  IoArrowBackOutline,
  IoCloudOutline,
  IoGitPullRequestOutline,
  IoOptionsOutline,
  IoSettingsOutline,
  IoStatsChartOutline,
} from "react-icons/io5"
import type { ComponentType, SVGProps } from "react"

import type { SessionUser } from "@/lib/api"
import { SidebarUserMenu } from "@/components/SidebarUserMenu"
import {
  SidebarCollapseButton,
  SidebarFrame,
  useSidebarLayout,
} from "@/components/sidebar-layout"
import { cn } from "@/lib/utils"

type IconType = ComponentType<SVGProps<SVGSVGElement>>

interface NavItem {
  to: string
  label: string
  icon: IconType
  adminOnly?: boolean
}

const NAV: Array<NavItem> = [
  { to: "/my-settings", label: "Profile Settings", icon: IoOptionsOutline },
  { to: "/cloud-agents", label: "Open SWE Agent", icon: IoCloudOutline },
  { to: "/usage", label: "Usage Leaderboard", icon: IoStatsChartOutline },
  { to: "/review", label: "Open SWE Review", icon: IoGitPullRequestOutline },
  { to: "/admin", label: "Admin", icon: IoSettingsOutline, adminOnly: true },
]

export function AppSidebar({ user }: { user: SessionUser }) {
  const layout = useSidebarLayout()
  return (
    <SidebarFrame
      {...layout}
      className="border-r border-border bg-sidebar text-sidebar-foreground"
    >
      <div className="flex items-center justify-between px-5 pt-6 pb-4 md:px-4 md:pt-5">
        <Link
          to="/my-settings"
          className="flex items-center gap-2 font-heading text-lg font-medium tracking-tight md:text-sm"
        >
          <img src="/logo-mark.png" alt="" className="size-6 md:size-5" />
          open-swe
        </Link>
        <SidebarCollapseButton onToggle={layout.toggle} />
      </div>

      <nav className="flex flex-1 flex-col gap-1 px-3 md:gap-0.5 md:px-2">
        <Link
          to="/agents"
          onClick={layout.closeOnMobile}
          className={cn(
            "flex min-h-11 touch-manipulation items-center gap-3 rounded-xl px-4 py-3 text-sm text-muted-foreground transition-colors md:min-h-0 md:gap-2.5 md:rounded-md md:px-2.5 md:py-1.5 md:text-xs/relaxed",
            "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
          )}
        >
          <IoArrowBackOutline className="size-5 md:size-4" />
          <span>Back to Agents</span>
        </Link>
        {NAV.filter((n) => !n.adminOnly || user.is_admin).map((item) => {
          const Icon = item.icon
          return (
            <Link
              key={item.to}
              to={item.to}
              onClick={layout.closeOnMobile}
              className={cn(
                "flex min-h-11 touch-manipulation items-center gap-3 rounded-xl px-4 py-3 text-sm text-muted-foreground transition-colors md:min-h-0 md:gap-2.5 md:rounded-md md:px-2.5 md:py-1.5 md:text-xs/relaxed",
                "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              )}
              activeProps={{
                className:
                  "bg-sidebar-accent text-sidebar-accent-foreground font-medium",
              }}
            >
              <Icon className="size-5 md:size-4" />
              <span>{item.label}</span>
            </Link>
          )
        })}
      </nav>

      <div className="p-3 md:p-2">
        <SidebarUserMenu user={user} />
      </div>
    </SidebarFrame>
  )
}
