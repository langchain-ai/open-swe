import { Link, useRouterState } from "@tanstack/react-router"
import {
  IoArrowBackOutline,
  IoBarChartOutline,
  IoBrushOutline,
  IoCubeOutline,
  IoDocumentTextOutline,
  IoExtensionPuzzleOutline,
  IoFlaskOutline,
  IoGitBranchOutline,
  IoGitPullRequestOutline,
  IoInformationCircleOutline,
  IoLinkOutline,
  IoOptionsOutline,
  IoPeopleOutline,
  IoPulseOutline,
  IoReaderOutline,
  IoSettingsOutline,
  IoSparklesOutline,
  IoStatsChartOutline,
  IoWarningOutline,
} from "react-icons/io5"
import { Fragment, type ComponentType, type SVGProps } from "react"

import { useWorkspaceOptions } from "@/features/agents/lib/queries"
import type { SessionUser } from "@/lib/api"
import { SidebarUserMenu } from "@/components/SidebarUserMenu"
import {
  SidebarCollapseButton,
  SidebarFrame,
  useSidebarLayout,
} from "@/components/sidebar-layout"
import { cn } from "@/lib/utils"
import { getLastAppLocation, useHrefLinkOptions } from "@/lib/appLocation"

type IconType = ComponentType<SVGProps<SVGSVGElement>>

interface NavItem {
  to: string
  label: string
  icon: IconType
  /** Child pages that also highlight this item. */
  childPrefix?: string
}

interface NavGroup {
  heading: string
  adminOnly?: boolean
  items: Array<NavItem>
}

const NAV: Array<NavGroup> = [
  {
    heading: "Personal",
    items: [
      { to: "/my-settings", label: "General", icon: IoSettingsOutline },
      { to: "/my-settings/agent", label: "Agent", icon: IoSparklesOutline },
      { to: "/my-settings/git", label: "Git", icon: IoGitBranchOutline },
      {
        to: "/my-settings/instructions",
        label: "Instructions",
        icon: IoReaderOutline,
      },
      {
        to: "/my-settings/connections",
        label: "Connections",
        icon: IoLinkOutline,
      },
      {
        to: "/my-settings/experiments",
        label: "Experiments",
        icon: IoFlaskOutline,
      },
    ],
  },
  {
    heading: "Workspace",
    items: [
      {
        to: "/review",
        label: "Code review",
        icon: IoGitPullRequestOutline,
        childPrefix: "/review/repositories/",
      },
      { to: "/review/styles", label: "Review styles", icon: IoBrushOutline },
      {
        to: "/agents/instructions",
        label: "Repository instructions",
        icon: IoDocumentTextOutline,
      },
      { to: "/workspaces", label: "Workspaces", icon: IoCubeOutline },
      { to: "/usage", label: "Usage", icon: IoStatsChartOutline },
    ],
  },
  {
    heading: "Administration",
    adminOnly: true,
    items: [
      { to: "/admin", label: "Defaults", icon: IoOptionsOutline },
      {
        to: "/admin/integrations",
        label: "Integrations",
        icon: IoExtensionPuzzleOutline,
      },
      { to: "/admin/incidents", label: "Incidents", icon: IoWarningOutline },
      { to: "/admin/operations", label: "Operations", icon: IoPulseOutline },
      { to: "/admin/audit-logs", label: "Audit logs", icon: IoReaderOutline },
      { to: "/admin/users", label: "Users", icon: IoPeopleOutline },
      { to: "/admin/evals", label: "Evals", icon: IoBarChartOutline },
    ],
  },
  {
    heading: "Help",
    items: [
      {
        to: "/my-settings/about",
        label: "About",
        icon: IoInformationCircleOutline,
      },
    ],
  },
]

function isActive(item: NavItem, pathname: string): boolean {
  if (pathname === item.to) return true
  return !!item.childPrefix && pathname.startsWith(item.childPrefix)
}

const LINK_CLASS =
  "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs/relaxed text-secondary transition-colors hover:bg-surface-level-2-hover hover:text-primary"

const ACTIVE_LINK_PROPS = {
  className: "bg-surface-level-2-hover text-primary font-medium",
}

function WorkspaceNavItems({ onNavigate }: { onNavigate: () => void }) {
  const options = useWorkspaceOptions()
  const workspaces = options.data?.workspaces ?? []
  if (workspaces.length === 0) return null
  return (
    <div className="ml-[1.1rem] flex flex-col gap-0.5 border-l border-default pl-2">
      {workspaces.map((workspace) => (
        <Link
          key={workspace.slug}
          to="/workspaces/$slug"
          params={{ slug: workspace.slug }}
          onClick={onNavigate}
          className={cn(LINK_CLASS, "py-1")}
          activeProps={ACTIVE_LINK_PROPS}
        >
          <span className="truncate">{workspace.name}</span>
        </Link>
      ))}
    </div>
  )
}

export function AppSidebar({ user }: { user: SessionUser }) {
  const layout = useSidebarLayout()
  const hrefLinkOptions = useHrefLinkOptions()
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)

  return (
    <SidebarFrame
      {...layout}
      className="border-r border-default bg-surface-level-2 text-primary"
    >
      <div
        className={cn(
          "flex items-center justify-between px-4 pb-4",
          isDesktop ? "pt-13" : "pt-5"
        )}
      >
        <Link
          {...hrefLinkOptions(getLastAppLocation())}
          className={cn(LINK_CLASS, "-mx-2.5 font-medium")}
          onClick={layout.closeOnMobile}
        >
          <IoArrowBackOutline className="size-4" />
          <span>Back to app</span>
        </Link>
        <SidebarCollapseButton onToggle={layout.toggle} />
      </div>

      <nav className="flex flex-1 flex-col gap-5 overflow-y-auto px-2">
        {NAV.filter((group) => !group.adminOnly || user.is_admin).map(
          (group) => (
            <div key={group.heading} className="flex flex-col gap-0.5">
              <span className="px-2.5 pb-1 text-[10px] font-medium tracking-wide text-tertiary uppercase">
                {group.heading}
              </span>
              {group.items.map((item) => {
                const Icon = item.icon
                return (
                  <Fragment key={item.to}>
                    <Link
                      to={item.to}
                      onClick={layout.closeOnMobile}
                      className={cn(
                        LINK_CLASS,
                        isActive(item, pathname) && ACTIVE_LINK_PROPS.className
                      )}
                    >
                      <Icon className="size-4" />
                      <span>{item.label}</span>
                    </Link>
                    {item.to === "/workspaces" && user.is_admin && (
                      <WorkspaceNavItems onNavigate={layout.closeOnMobile} />
                    )}
                  </Fragment>
                )
              })}
            </div>
          )
        )}
      </nav>

      <div className="p-2">
        <SidebarUserMenu user={user} />
      </div>
    </SidebarFrame>
  )
}
