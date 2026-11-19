import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { useScrollIntoView } from "@/lib/useScrollIntoView"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useProfile } from "@/lib/profile"
import { useSession } from "@/lib/session"
import { SlackLinks } from "@/lib/slack-links"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import { LightningIcon } from "@phosphor-icons/react/dist/ssr/Lightning"
import { SparkleIcon } from "@phosphor-icons/react/dist/ssr/Sparkle"
import { Link, useRouterState } from "@tanstack/react-router"
import { useRef } from "react"

import type { IconComponent } from "@langchain/macaw-components/Icon"

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
  const items: ReadonlyArray<NavItem> = [
    ...NAV,
    ...(plugins.data ?? []).map((plugin) => ({
      to: pluginRoot(plugin.name),
      label: plugin.manifest.sidebar.label,
      icon: pluginIcon(plugin.manifest.sidebar.icon),
    })),
  ]

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
      <div
        className={cn("shrink-0 px-space-2", containerClassName)}
        data-sidebar-loading={plugins.isLoading ? "" : undefined}
      >
        <nav className={cn("flex flex-col gap-0.5", className)}>
          {items.map((item) => (
            <SidebarNavLink
              key={item.to}
              item={item}
              href={target(item.to)}
              active={activeSection === item.to}
              onNavigate={onNavigate}
            />
          ))}
          {plugins.isLoading && (
            <div
              data-sidebar-loading
              role="status"
              aria-label="Loading apps"
              className="flex h-8 items-center px-space-2"
            >
              <Skeleton className="h-2.5 w-24" />
            </div>
          )}
        </nav>
      </div>
    </>
  )
}
