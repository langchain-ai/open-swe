import { Button } from "@langchain/macaw-components/Button"
import { CaretRightIcon } from "@langchain/macaw-components/icons"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import { LightningIcon } from "@phosphor-icons/react/dist/ssr/Lightning"
import { PencilSimpleIcon } from "@phosphor-icons/react/dist/ssr/PencilSimple"
import { RobotIcon } from "@phosphor-icons/react/dist/ssr/Robot"
import { SirenIcon } from "@phosphor-icons/react/dist/ssr/Siren"
import { SparkleIcon } from "@phosphor-icons/react/dist/ssr/Sparkle"
import { Link, useRouterState } from "@tanstack/react-router"

import type { IconComponent } from "@langchain/macaw-components/Icon"

import { DropdownMenuItem, DropdownMenuSubTrigger } from "@/components/MenuItem"
import { MenuCheckItem } from "@/features/agents/components/MenuCheckItem"
import { useSidebarPrefs } from "@/features/agents/lib/sidebarPrefs"
import { pluginIcon, pluginRoot } from "@/features/plugins/lib/icons"
import { usePlugins } from "@/features/plugins/lib/queries"
import type { SectionRoot } from "@/lib/appLocation"
import {
  getLastSectionLocation,
  sectionOf,
  useHrefLinkOptions,
} from "@/lib/appLocation"
import { cn } from "@/lib/utils"

interface NavItem {
  to: SectionRoot
  label: string
  icon: IconComponent
}

const NAV: ReadonlyArray<NavItem> = [
  { to: "/agents/skills", label: "Skills", icon: SparkleIcon },
  { to: "/agents/automations", label: "Automations", icon: LightningIcon },
  { to: "/agents/bots", label: "Bots", icon: RobotIcon },
  { to: "/agents/reviews", label: "Pull Requests", icon: GitPullRequestIcon },
  { to: "/incidents", label: "Incidents", icon: SirenIcon },
]

const ROW =
  "flex w-full items-center gap-space-3 rounded-md px-space-2 py-space-1 text-sm text-primary transition-colors hover:bg-surface-level-2-hover"
const SELECTED_ROW = "bg-selected font-medium hover:bg-selected-hover"

export function SidebarNav({
  className,
  onNavigate,
}: {
  className?: string
  onNavigate: () => void
}) {
  const { prefs, toggleNavItemHidden } = useSidebarPrefs()
  const plugins = usePlugins()
  const activeSection = useRouterState({
    select: (state) => sectionOf(state.location.pathname),
  })
  const linkTarget = useHrefLinkOptions()
  const items: ReadonlyArray<NavItem> = [
    ...NAV,
    ...(plugins.data ?? []).map((plugin) => ({
      to: pluginRoot(plugin.name),
      label: plugin.manifest.sidebar.label,
      icon: pluginIcon(plugin.manifest.sidebar.icon),
    })),
  ]
  const hidden = items.filter((item) => prefs.hiddenNavItems.includes(item.to))
  const target = (to: SectionRoot) =>
    linkTarget(activeSection === to ? to : getLastSectionLocation(to))

  return (
    <nav className={cn("flex flex-col gap-0.5", className)}>
      {items
        .filter((item) => !hidden.includes(item))
        .map((item) => (
          <Link
            key={item.to}
            {...target(item.to)}
            onClick={onNavigate}
            className={cn(ROW, activeSection === item.to && SELECTED_ROW)}
          >
            <item.icon size={16} weight="regular" className="shrink-0" />
            {item.label}
          </Link>
        ))}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            color="secondary"
            variant="plain"
            size="sm"
            aria-label="More"
            type="button"
            className={cn(
              ROW,
              // Match the Link rows' height and hover; Macaw's plain variant adds its own dark hover.
              "h-auto justify-start text-secondary data-[state=open]:bg-surface-level-2-hover dark:hover:bg-surface-level-2-hover",
              hidden.some((item) => item.to === activeSection) &&
                cn(SELECTED_ROW, "text-primary dark:hover:bg-selected-hover")
            )}
          >
            <CaretRightIcon size={16} weight="regular" className="shrink-0" />
            More
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="right" align="start" className="w-48">
          {hidden.map((item) => (
            <DropdownMenuItem key={item.to} asChild>
              <Link {...target(item.to)} onClick={onNavigate}>
                <item.icon size={14} weight="regular" className="shrink-0" />
                {item.label}
              </Link>
            </DropdownMenuItem>
          ))}
          {hidden.length > 0 && <DropdownMenuSeparator />}
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>
              <PencilSimpleIcon size={14} weight="regular" />
              Edit sidebar
              <CaretRightIcon
                size={12}
                weight="regular"
                className="ml-auto text-icon-secondary"
              />
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="w-48">
              {items.map((item) => (
                <MenuCheckItem
                  key={item.to}
                  checked={!hidden.includes(item)}
                  onCheckedChange={() => toggleNavItemHidden(item.to)}
                >
                  {item.label}
                </MenuCheckItem>
              ))}
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        </DropdownMenuContent>
      </DropdownMenu>
    </nav>
  )
}
