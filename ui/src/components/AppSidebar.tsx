import {
  ArrowLeftIcon,
  ArticleRegularIcon,
  CubeRegularIcon,
  GearRegularIcon,
  InfoRegularIcon,
  SlidersHorizontalRegularIcon,
  WarningRegularIcon,
} from "@langchain/macaw-components/icons"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { ChartBarIcon } from "@phosphor-icons/react/dist/ssr/ChartBar"
import { ChartBarHorizontalIcon } from "@phosphor-icons/react/dist/ssr/ChartBarHorizontal"
import { FileTextIcon } from "@phosphor-icons/react/dist/ssr/FileText"
import { FlaskIcon } from "@phosphor-icons/react/dist/ssr/Flask"
import { GitBranchIcon } from "@phosphor-icons/react/dist/ssr/GitBranch"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import { LinkIcon } from "@phosphor-icons/react/dist/ssr/Link"
import { PaintBrushIcon } from "@phosphor-icons/react/dist/ssr/PaintBrush"
import { PulseIcon } from "@phosphor-icons/react/dist/ssr/Pulse"
import { PuzzlePieceIcon } from "@phosphor-icons/react/dist/ssr/PuzzlePiece"
import { SparkleIcon } from "@phosphor-icons/react/dist/ssr/Sparkle"
import { UsersIcon } from "@phosphor-icons/react/dist/ssr/Users"
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
      { to: "/my-settings", label: "General", icon: GearRegularIcon },
      { to: "/my-settings/agent", label: "Agent", icon: SparkleIcon },
      { to: "/my-settings/git", label: "Git", icon: GitBranchIcon },
      {
        to: "/my-settings/instructions",
        label: "Instructions",
        icon: ArticleRegularIcon,
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
        label: "Code Review",
        icon: GitPullRequestIcon,
        childPrefix: "/review/repositories/",
      },
      { to: "/review/styles", label: "Review Styles", icon: PaintBrushIcon },
      {
        to: "/agents/instructions",
        label: "Repository Instructions",
        icon: FileTextIcon,
      },
      { to: "/workspaces", label: "Workspaces", icon: CubeRegularIcon },
      { to: "/usage", label: "Usage", icon: ChartBarIcon },
    ],
  },
  {
    heading: "Administration",
    adminOnly: true,
    items: [
      { to: "/admin", label: "Defaults", icon: SlidersHorizontalRegularIcon },
      {
        to: "/admin/integrations",
        label: "Integrations",
        icon: PuzzlePieceIcon,
      },
      { to: "/admin/incidents", label: "Incidents", icon: WarningRegularIcon },
      { to: "/admin/operations", label: "Operations", icon: PulseIcon },
      {
        to: "/admin/audit-logs",
        label: "Audit Logs",
        icon: ArticleRegularIcon,
      },
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
        icon: InfoRegularIcon,
      },
    ],
  },
]

function isActive(item: NavItem, pathname: string): boolean {
  if (pathname === item.to) return true
  return !!item.childPrefix && pathname.startsWith(item.childPrefix)
}

const LINK_CLASS =
  "flex items-center gap-space-3 rounded-md px-space-2 py-space-1 text-xs/relaxed text-secondary transition-colors hover:bg-surface-level-2-hover hover:text-primary"

const ACTIVE_LINK_PROPS = {
  className: "bg-surface-level-2-hover text-primary font-medium",
}

function WorkspaceNavItems({ onNavigate }: { onNavigate: () => void }) {
  const options = useWorkspaceOptions()
  const workspaces = options.data?.workspaces ?? []
  if (workspaces.length === 0) return null
  return (
    <div className="ml-[1.1rem] flex flex-col gap-0.5 border-l border-default pl-space-2">
      {workspaces.map((workspace) => (
        <Link
          key={workspace.slug}
          to="/workspaces/$slug"
          params={{ slug: workspace.slug }}
          onClick={onNavigate}
          className={cn(LINK_CLASS, "py-space-1")}
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
          "flex items-center justify-between px-space-4 pb-space-4",
          isDesktop ? "pt-13" : "pt-space-4"
        )}
      >
        <Link
          {...hrefLinkOptions(getLastAppLocation())}
          className={cn(LINK_CLASS, "-mx-space-2 font-medium")}
          onClick={layout.closeOnMobile}
        >
          <ArrowLeftIcon size={16} weight="regular" />
          <span>Back to app</span>
        </Link>
        <SidebarCollapseButton onToggle={layout.toggle} />
      </div>

      <nav className="flex flex-1 flex-col gap-space-5 overflow-y-auto px-space-2">
        {NAV.filter((group) => !group.adminOnly || user.is_admin).map(
          (group) => (
            <div key={group.heading} className="flex flex-col gap-0.5">
              <span className="px-space-2 pb-space-1 text-xxs font-medium tracking-wide text-tertiary uppercase">
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

      <div className="p-space-2">
        <SidebarUserMenu user={user} />
      </div>
    </SidebarFrame>
  )
}
