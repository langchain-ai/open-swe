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

const NAV: Array<{ heading: string; items: Array<NavItem> }> = [
  {
    heading: "Personal",
    items: [
      { to: "/my-settings", label: "Settings", icon: IoOptionsOutline },
      { to: "/usage", label: "Usage", icon: IoStatsChartOutline },
    ],
  },
  {
    heading: "Workspace",
    items: [
      { to: "/cloud-agents", label: "Open SWE Agent", icon: IoCloudOutline },
      {
        to: "/review",
        label: "Open SWE Review",
        icon: IoGitPullRequestOutline,
      },
      {
        to: "/admin",
        label: "Admin",
        icon: IoSettingsOutline,
        adminOnly: true,
      },
    ],
  },
]

const LINK_CLASS =
  "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs/relaxed text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"

export function AppSidebar({ user }: { user: SessionUser }) {
  const layout = useSidebarLayout()
  return (
    <SidebarFrame
      {...layout}
      className="border-r border-border bg-sidebar text-sidebar-foreground"
    >
      <div className="flex items-center justify-between px-4 pt-5 pb-4">
        <Link
          to="/agents"
          className={cn(LINK_CLASS, "-mx-2.5 font-medium")}
          onClick={layout.closeOnMobile}
        >
          <IoArrowBackOutline className="size-4" />
          <span>Back to app</span>
        </Link>
        <SidebarCollapseButton onToggle={layout.toggle} />
      </div>

      <nav className="flex flex-1 flex-col gap-5 px-2">
        {NAV.map((group) => {
          const items = group.items.filter((i) => !i.adminOnly || user.is_admin)
          if (items.length === 0) return null
          return (
            <div key={group.heading} className="flex flex-col gap-0.5">
              <span className="px-2.5 pb-1 text-[10px] font-medium tracking-wide text-muted-foreground/70 uppercase">
                {group.heading}
              </span>
              {items.map((item) => {
                const Icon = item.icon
                return (
                  <Link
                    key={item.to}
                    to={item.to}
                    onClick={layout.closeOnMobile}
                    className={LINK_CLASS}
                    activeProps={{
                      className:
                        "bg-sidebar-accent text-sidebar-accent-foreground font-medium",
                    }}
                  >
                    <Icon className="size-4" />
                    <span>{item.label}</span>
                  </Link>
                )
              })}
            </div>
          )
        })}
      </nav>

      <div className="p-2">
        <SidebarUserMenu user={user} />
      </div>
    </SidebarFrame>
  )
}
