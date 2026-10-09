import type { IconComponent } from "@langchain/macaw-components/Icon"
import { ArrowLeftIcon } from "@phosphor-icons/react/dist/ssr/ArrowLeft"
import { ArticleIcon } from "@phosphor-icons/react/dist/ssr/Article"
import { ChartBarIcon } from "@phosphor-icons/react/dist/ssr/ChartBar"
import { ChartBarHorizontalIcon } from "@phosphor-icons/react/dist/ssr/ChartBarHorizontal"
import { CubeIcon } from "@phosphor-icons/react/dist/ssr/Cube"
import { FileTextIcon } from "@phosphor-icons/react/dist/ssr/FileText"
import { FlaskIcon } from "@phosphor-icons/react/dist/ssr/Flask"
import { GearIcon } from "@phosphor-icons/react/dist/ssr/Gear"
import { GitBranchIcon } from "@phosphor-icons/react/dist/ssr/GitBranch"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import { InfoIcon } from "@phosphor-icons/react/dist/ssr/Info"
import { LinkIcon } from "@phosphor-icons/react/dist/ssr/Link"
import { PaintBrushIcon } from "@phosphor-icons/react/dist/ssr/PaintBrush"
import { PulseIcon } from "@phosphor-icons/react/dist/ssr/Pulse"
import { PuzzlePieceIcon } from "@phosphor-icons/react/dist/ssr/PuzzlePiece"
import { SlidersHorizontalIcon } from "@phosphor-icons/react/dist/ssr/SlidersHorizontal"
import { SparkleIcon } from "@phosphor-icons/react/dist/ssr/Sparkle"
import { UsersIcon } from "@phosphor-icons/react/dist/ssr/Users"
import { WarningIcon } from "@phosphor-icons/react/dist/ssr/Warning"
import { Link, useRouterState } from "@tanstack/react-router"
import { Fragment } from "react"

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

interface NavItem {
  to: string
  label: string
  icon: IconComponent
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
      { to: "/my-settings", label: "General", icon: GearIcon },
      { to: "/my-settings/agent", label: "Agent", icon: SparkleIcon },
      { to: "/my-settings/git", label: "Git", icon: GitBranchIcon },
      {
        to: "/my-settings/instructions",
        label: "Instructions",
        icon: ArticleIcon,
      },
      {
        to: "/my-settings/connections",
        label: "Connections",
        icon: LinkIcon,
      },
      {
        to: "/my-settings/experiments",
        label: "Experiments",
        icon: FlaskIcon,
      },
    ],
  },
  {
    heading: "Workspace",
    items: [
      {
        to: "/review",
        label: "Code review",
        icon: GitPullRequestIcon,
        childPrefix: "/review/repositories/",
      },
      { to: "/review/styles", label: "Review styles", icon: PaintBrushIcon },
      {
        to: "/agents/instructions",
        label: "Repository instructions",
        icon: FileTextIcon,
      },
      { to: "/workspaces", label: "Workspaces", icon: CubeIcon },
      { to: "/usage", label: "Usage", icon: ChartBarIcon },
    ],
  },
  {
    heading: "Administration",
    adminOnly: true,
    items: [
      { to: "/admin", label: "Defaults", icon: SlidersHorizontalIcon },
      {
        to: "/admin/integrations",
        label: "Integrations",
        icon: PuzzlePieceIcon,
      },
      { to: "/admin/incidents", label: "Incidents", icon: WarningIcon },
      { to: "/admin/operations", label: "Operations", icon: PulseIcon },
      { to: "/admin/audit-logs", label: "Audit logs", icon: ArticleIcon },
      { to: "/admin/users", label: "Users", icon: UsersIcon },
      { to: "/admin/evals", label: "Evals", icon: ChartBarHorizontalIcon },
    ],
  },
  {
    heading: "Help",
    items: [
      {
        to: "/my-settings/about",
        label: "About",
        icon: InfoIcon,
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
          <ArrowLeftIcon size={16} weight="regular" />
          <span>Back to app</span>
        </Link>
        <SidebarCollapseButton onToggle={layout.toggle} />
      </div>

      <nav className="flex flex-1 flex-col gap-5 overflow-y-auto px-2">
        {NAV.filter((group) => !group.adminOnly || user.is_admin).map(
          (group) => (
            <div key={group.heading} className="flex flex-col gap-0.5">
              <span className="px-2.5 pb-1 text-xxs font-medium tracking-wide text-tertiary uppercase">
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
                      <Icon size={16} weight="regular" />
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
