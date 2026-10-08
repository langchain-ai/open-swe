import { Link, useRouterState } from "@tanstack/react-router"
import {
  CaretRightIcon,
  GitPullRequestIcon,
  LightningIcon,
  PencilSimpleIcon,
  RobotIcon,
  SparkleIcon,
} from "@phosphor-icons/react"
import { Radar } from "lucide-react"

import {
  Menu,
  MenuCheckboxItem,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuSub,
  MenuSubPopup,
  MenuSubTrigger,
  MenuTrigger,
} from "@/components/ui/menu"
import { useSidebarPrefs } from "@/features/agents/lib/sidebarPrefs"
import {
  getLastSectionLocation,
  sectionOf,
  useHrefLinkOptions,
} from "@/lib/appLocation"
import { cn } from "@/lib/utils"

const NAV = [
  { to: "/agents/skills", label: "Skills", icon: SparkleIcon },
  { to: "/agents/automations", label: "Automations", icon: LightningIcon },
  { to: "/agents/bots", label: "Bots", icon: RobotIcon },
  { to: "/agents/reviews", label: "Pull Requests", icon: GitPullRequestIcon },
  { to: "/incidents", label: "Incidents", icon: Radar },
] as const

const ROW =
  "flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm text-primary transition-colors hover:bg-surface-level-2-hover"

export function SidebarNav({
  className,
  onNavigate,
}: {
  className?: string
  onNavigate: () => void
}) {
  const { prefs, toggleNavItemHidden } = useSidebarPrefs()
  const activeSection = useRouterState({
    select: (state) => sectionOf(state.location.pathname),
  })
  const linkTarget = useHrefLinkOptions()
  const hidden = NAV.filter((item) => prefs.hiddenNavItems.includes(item.to))
  const target = (to: (typeof NAV)[number]["to"]) =>
    linkTarget(activeSection === to ? to : getLastSectionLocation(to))

  return (
    <nav className={cn("flex flex-col gap-0.5", className)}>
      {NAV.filter((item) => !hidden.includes(item)).map((item) => (
        <Link
          key={item.to}
          {...target(item.to)}
          onClick={onNavigate}
          className={cn(
            ROW,
            activeSection === item.to && "bg-surface-level-2-hover font-medium"
          )}
        >
          <item.icon className="size-4" />
          {item.label}
        </Link>
      ))}
      <Menu>
        <MenuTrigger
          className={cn(
            ROW,
            "text-secondary data-popup-open:bg-surface-level-2-hover",
            hidden.some((item) => item.to === activeSection) &&
              "bg-surface-level-2-hover font-medium text-primary"
          )}
        >
          <CaretRightIcon className="size-4" />
          More
        </MenuTrigger>
        <MenuPopup side="right" align="start" className="w-48">
          {hidden.map((item) => (
            <MenuItem
              key={item.to}
              render={<Link {...target(item.to)} onClick={onNavigate} />}
            >
              <item.icon />
              {item.label}
            </MenuItem>
          ))}
          {hidden.length > 0 && <MenuSeparator />}
          <MenuSub>
            <MenuSubTrigger>
              <PencilSimpleIcon />
              Edit sidebar
            </MenuSubTrigger>
            <MenuSubPopup className="w-48">
              {NAV.map((item) => (
                <MenuCheckboxItem
                  key={item.to}
                  checked={!hidden.includes(item)}
                  onCheckedChange={() => toggleNavItemHidden(item.to)}
                >
                  {item.label}
                </MenuCheckboxItem>
              ))}
            </MenuSubPopup>
          </MenuSub>
        </MenuPopup>
      </Menu>
    </nav>
  )
}
