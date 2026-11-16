import { useScrollIntoView } from "@/lib/useScrollIntoView"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useProfile } from "@/lib/profile"
import { useSession } from "@/lib/session"
import { SlackLinks } from "@/lib/slack-links"
import { Button } from "@langchain/macaw-components/Button"
import { CaretRightIcon } from "@langchain/macaw-components/icons"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { DotsThreeIcon } from "@phosphor-icons/react/dist/ssr/DotsThree"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import { LightningIcon } from "@phosphor-icons/react/dist/ssr/Lightning"
import { PencilSimpleIcon } from "@phosphor-icons/react/dist/ssr/PencilSimple"
import { SparkleIcon } from "@phosphor-icons/react/dist/ssr/Sparkle"
import { Link, useRouterState } from "@tanstack/react-router"
import { useRef } from "react"

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
  { to: "/agents/reviews", label: "Pull Requests", icon: GitPullRequestIcon },
]

const ROW =
  "flex w-full items-center gap-space-3 rounded-md px-space-2 py-space-1 text-sm text-primary transition-colors hover:bg-surface-level-2-hover"
const SELECTED_ROW = "bg-selected hover:bg-selected-hover"

function SidebarNavLink({
  item,
  href,
  active,
  onNavigate,
}: {
  item: NavItem
  href: ReturnType<ReturnType<typeof useHrefLinkOptions>>
  active: boolean
  onNavigate: () => void
}) {
  const ref = useRef<HTMLAnchorElement>(null)
  useScrollIntoView(ref, active)

  return (
    <Link
      {...href}
      ref={ref}
      onClick={onNavigate}
      className={cn(ROW, active && SELECTED_ROW)}
    >
      <item.icon size={16} weight="regular" className="shrink-0" />
      {item.label}
    </Link>
  )
}

export function SidebarNav({
  className,
  containerClassName,
  activeThreadId,
  onNavigate,
}: {
  className?: string
  containerClassName?: string
  activeThreadId?: string
  onNavigate: () => void
}) {
  const { prefs, toggleNavItemHidden } = useSidebarPrefs()
  const plugins = usePlugins()
  const session = useSession()
  const profile = useProfile()

  const concierge = useQuery({
    queryKey: ["concierge", session.data?.login],
    queryFn: api.concierge,
    enabled: !!profile.data?.concierge_mode,
    // oxlint-disable-next-line invalidations/no-polling
    refetchInterval: 30_000,
  })

  const activeSection = useRouterState({
    select: (state) => sectionOf(state.location.pathname),
  })
  const conciergeRef = useRef<HTMLAnchorElement>(null)
  useScrollIntoView(
    conciergeRef,
    Boolean(
      concierge.data?.thread_id && activeThreadId === concierge.data.thread_id
    )
  )
  const linkTarget = useHrefLinkOptions()
  // Keep focus on the page when navigation closes the menu.
  const navigated = useRef(false)
  const items: ReadonlyArray<NavItem> = [
    ...NAV,
    ...(plugins.data ?? []).map((plugin) => ({
      to: pluginRoot(plugin.name),
      label: plugin.manifest.sidebar.label,
      icon: pluginIcon(plugin.manifest.sidebar.icon),
    })),
  ]

  const hidden = items.filter(
    (item) =>
      prefs.hiddenNavItems.includes(item.to) && item.to !== activeSection
  )

  const target = (to: SectionRoot) =>
    linkTarget(activeSection === to ? to : getLastSectionLocation(to))

  return (
    <>
      {profile.data?.concierge_mode && (
        <div
          className={cn(
            "flex flex-col gap-0.5 px-space-2 pb-space-1",
            containerClassName
          )}
        >
          {concierge.data?.thread_id ? (
            <Link
              to="/agents/$threadId"
              params={{ threadId: concierge.data.thread_id }}
              ref={conciergeRef}
              onClick={onNavigate}
              aria-current={
                activeThreadId === concierge.data.thread_id ? "page" : undefined
              }
              className={cn(
                ROW,
                activeThreadId === concierge.data.thread_id && SELECTED_ROW
              )}
            >
              <ChatCircleIcon size={16} weight="regular" />
              Concierge
            </Link>
          ) : (
            <a
              href={
                concierge.data?.channel_id
                  ? SlackLinks.open(concierge.data.channel_id)
                  : undefined
              }
              onClick={onNavigate}
              className={ROW}
            >
              <ChatCircleIcon size={16} weight="regular" />
              Concierge
            </a>
          )}
        </div>
      )}
      <div className={cn("shrink-0 px-space-2", containerClassName)}>
        <nav className={cn("flex flex-col gap-0.5", className)}>
          {items
            .filter((item) => !hidden.includes(item))
            .map((item) => (
              <SidebarNavLink
                key={item.to}
                item={item}
                href={target(item.to)}
                active={activeSection === item.to}
                onNavigate={onNavigate}
              />
            ))}
          {plugins.isLoading ? (
            <div
              data-sidebar-loading
              role="status"
              aria-label="Loading apps"
              className="flex h-8 items-center px-space-2"
            >
              <Skeleton className="h-2.5 w-24" />
            </div>
          ) : (
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
                    // Match the Link rows' box and hover: Macaw's plain variant adds a
                    // 1px transparent border that shifts the label, and its own dark hover.
                    "h-auto justify-start border-0 text-secondary data-[state=open]:bg-surface-level-2-hover dark:hover:bg-surface-level-2-hover"
                  )}
                >
                  <DotsThreeIcon size={16} weight="bold" className="shrink-0" />
                  More
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent
                side="right"
                align="start"
                className="w-48"
                onCloseAutoFocus={(event) => {
                  if (navigated.current) event.preventDefault()
                  navigated.current = false
                }}
              >
                {hidden.map((item) => (
                  <DropdownMenuItem key={item.to} asChild>
                    <Link
                      {...target(item.to)}
                      onClick={() => {
                        navigated.current = true
                        onNavigate()
                      }}
                    >
                      <item.icon
                        size={14}
                        weight="regular"
                        className="shrink-0"
                      />
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
                        checked={!prefs.hiddenNavItems.includes(item.to)}
                        onCheckedChange={() => toggleNavItemHidden(item.to)}
                      >
                        {item.label}
                      </MenuCheckItem>
                    ))}
                  </DropdownMenuSubContent>
                </DropdownMenuSub>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </nav>
      </div>
    </>
  )
}
