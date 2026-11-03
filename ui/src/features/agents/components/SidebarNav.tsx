import { Button } from "@langchain/macaw-components/Button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { WrenchIcon } from "@phosphor-icons/react/dist/ssr/Wrench"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import { LightningIcon } from "@phosphor-icons/react/dist/ssr/Lightning"
import { RobotIcon } from "@phosphor-icons/react/dist/ssr/Robot"
import { SirenIcon } from "@phosphor-icons/react/dist/ssr/Siren"
import { SparkleIcon } from "@phosphor-icons/react/dist/ssr/Sparkle"
import { Link } from "@tanstack/react-router"
import { DropdownMenuItem } from "@/components/MenuItem"
import { cn } from "@/lib/utils"

const NAV = [
  { to: "/agents/skills", label: "Skills", icon: SparkleIcon },
  { to: "/agents/automations", label: "Automations", icon: LightningIcon },
  { to: "/agents/reviews", label: "Code Review", icon: GitPullRequestIcon },
  { to: "/agents/bots", label: "Bots", icon: RobotIcon },
  { to: "/incidents", label: "Incidents", icon: SirenIcon },
] as const

export function SidebarNav({
  className,
  onNavigate,
  onNewProject,
}: {
  className?: string
  onNavigate: () => void
  onNewProject?: () => void
}) {
  return (
    <nav className={className}>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            size="sm"
            color="secondary"
            variant="plain"
            aria-label="Tools"
            className={cn("w-full justify-start gap-space-3 text-secondary")}
          >
            <WrenchIcon size={16} weight="regular" />
            Tools
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="right" align="end" className="w-48">
          {NAV.map((item, index) => (
            <div key={item.to}>
              {index === 3 && <DropdownMenuSeparator />}
              <DropdownMenuItem asChild>
                <Link to={item.to} onClick={onNavigate}>
                  <item.icon size={14} weight="regular" />
                  {item.label}
                </Link>
              </DropdownMenuItem>
            </div>
          ))}
          {onNewProject && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={onNewProject}>
                New Project
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </nav>
  )
}
