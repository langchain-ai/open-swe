import { useRouterState } from "@tanstack/react-router"
import {
  SidebarNav,
  SidebarNavChild,
  SidebarNavCluster,
  SidebarNavItem,
} from "@langchain/gtm-platform-design-system/patterns/sidebar-nav"

import type { SessionUser } from "@/lib/api"
import type { Glyph } from "@/components/glyphs"
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  BarChart,
  Beaker,
  BookOpen,
  Brush,
  Cube,
  FileText,
  GitBranch,
  GitPullRequest,
  Info,
  Link,
  Puzzle,
  Settings2,
  Sliders,
  Sparkles,
  TrendingUp,
  Users,
} from "@/components/glyphs"
import { useWorkspaceOptions } from "@/features/agents/lib/queries"
import { getLastAppLocation } from "@/lib/appLocation"

interface NavItem {
  to: string
  label: string
  icon: Glyph
  /** Child pages that also select this item. */
  childPrefix?: string
}

interface NavGroup {
  heading: string
  adminOnly?: boolean
  items: ReadonlyArray<NavItem>
}

const NAV: ReadonlyArray<NavGroup> = [
  {
    heading: "Personal",
    items: [
      { to: "/my-settings", label: "General", icon: Settings2 },
      { to: "/my-settings/agent", label: "Agent", icon: Sparkles },
      { to: "/my-settings/git", label: "Git", icon: GitBranch },
      {
        to: "/my-settings/instructions",
        label: "Instructions",
        icon: BookOpen,
      },
      { to: "/my-settings/connections", label: "Connections", icon: Link },
      { to: "/my-settings/experiments", label: "Experiments", icon: Beaker },
    ],
  },
  {
    heading: "Workspace",
    items: [
      {
        to: "/review",
        label: "Code review",
        icon: GitPullRequest,
        childPrefix: "/review/repositories/",
      },
      { to: "/review/styles", label: "Review styles", icon: Brush },
      {
        to: "/agents/instructions",
        label: "Repository instructions",
        icon: FileText,
      },
      { to: "/workspaces", label: "Workspaces", icon: Cube },
      { to: "/usage", label: "Usage", icon: TrendingUp },
    ],
  },
  {
    heading: "Administration",
    adminOnly: true,
    items: [
      { to: "/admin", label: "Defaults", icon: Sliders },
      { to: "/admin/integrations", label: "Integrations", icon: Puzzle },
      { to: "/admin/incidents", label: "Incidents", icon: AlertTriangle },
      { to: "/admin/operations", label: "Operations", icon: Activity },
      { to: "/admin/users", label: "Users", icon: Users },
      { to: "/admin/evals", label: "Evals", icon: BarChart },
    ],
  },
  {
    heading: "Help",
    items: [{ to: "/my-settings/about", label: "About", icon: Info }],
  },
]

function isSelected(item: NavItem, pathname: string): boolean {
  if (pathname === item.to || pathname === `${item.to}/`) return true
  return Boolean(item.childPrefix && pathname.startsWith(item.childPrefix))
}

function WorkspaceChildren({ pathname }: { pathname: string }) {
  const options = useWorkspaceOptions()
  const workspaces = options.data?.workspaces ?? []
  return workspaces.map((workspace) => {
    const href = `/workspaces/${workspace.slug}`
    return (
      <SidebarNavChild
        key={workspace.slug}
        label={workspace.name}
        href={href}
        selected={pathname === href}
      />
    )
  })
}

/** The settings scoped rail: Back to app, then one cluster per settings family. */
export function SettingsNav({
  user,
  collapsed,
}: {
  user: SessionUser
  collapsed: boolean
}) {
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  return (
    <SidebarNav label="Settings">
      <SidebarNavCluster>
        <SidebarNavItem
          icon={ArrowLeft}
          label="Back to app"
          href={getLastAppLocation()}
          compact={collapsed}
        />
      </SidebarNavCluster>
      {NAV.filter((group) => !group.adminOnly || user.is_admin).map((group) => (
        <SidebarNavCluster
          key={group.heading}
          label={collapsed ? null : group.heading}
          divider={collapsed}
        >
          {group.items.map((item) => (
            <SidebarNavItem
              key={item.to}
              icon={item.icon}
              label={item.label}
              href={item.to}
              selected={isSelected(item, pathname)}
              compact={collapsed}
            >
              {item.to === "/workspaces" && user.is_admin && !collapsed ? (
                <WorkspaceChildren pathname={pathname} />
              ) : undefined}
            </SidebarNavItem>
          ))}
        </SidebarNavCluster>
      ))}
    </SidebarNav>
  )
}
