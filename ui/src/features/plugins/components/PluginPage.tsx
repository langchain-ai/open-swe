import { Button } from "@langchain/macaw-components/Button"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { ErrorState } from "@langchain/macaw-components/ErrorState"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { ArrowsOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowsOut"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { PuzzlePieceIcon } from "@phosphor-icons/react/dist/ssr/PuzzlePiece"
import { ShieldCheckIcon } from "@phosphor-icons/react/dist/ssr/ShieldCheck"
import { Link, useNavigate } from "@tanstack/react-router"
import { useEffect, useEffectEvent, useRef, useState } from "react"

import { SideSheet } from "@/components/SideSheet"
import { RightPanelShell } from "@/features/agents/components/panel/RightPanelShell"
import { RIGHT_PANEL_SHEET_CLASS_NAME } from "@/features/agents/components/panel/rightPanelLayout"
import { PluginAgentDock } from "@/features/plugins/components/PluginAgentDock"
import type { ProvidedTools } from "@/features/plugins/components/PluginAgentDock"
import { PluginView } from "@/features/plugins/components/PluginView"
import type { InstalledPlugin } from "@/features/plugins/lib/api"
import type {
  AgentToolOffer,
  ChatTarget,
  SharedContext,
  ToolRunner,
} from "@/features/plugins/lib/bridge"
import { PluginIcon } from "@/features/plugins/lib/icons"
import { pageTitle } from "@/lib/pageTitle"
import { useMediaQuery } from "@/lib/useIsMobile"
import { usePlugin } from "@/features/plugins/lib/queries"

/**
 * One installed plugin's page. `active` is whether it is the page on screen: a
 * plugin stays mounted while the person is elsewhere, like a tab, and while it
 * is hidden it can neither move the web UI nor ask the person anything.
 * `onPath` hears every path the plugin moves to, on screen or not.
 */
export function PluginPage({
  name,
  path,
  active,
  onPath,
}: {
  name: string
  path: string
  active: boolean
  onPath: (path: string) => void
}) {
  const { plugin, isPending, error } = usePlugin(name)
  if (isPending)
    return (
      <main className="flex min-w-0 flex-1 p-space-5">
        <Skeleton className="h-full w-full" />
      </main>
    )
  if (error)
    return (
      <main className="flex min-w-0 flex-1 items-center justify-center p-space-5">
        <ErrorState
          title="Couldn't load apps"
          message={error.message}
          action={null}
        />
      </main>
    )
  if (!plugin)
    return (
      <main className="flex min-w-0 flex-1 items-center justify-center p-space-5">
        <EmptyState
          icon={PuzzlePieceIcon}
          title="App not in your sidebar"
          description={`You haven't added an app named "${name}".`}
          action={
            <Button
              size="sm"
              variant="outlined"
              as={<Link to="/my-settings/plugins" />}
            >
              Manage apps
            </Button>
          }
        />
      </main>
    )
  return (
    <InstalledPluginPage
      key={plugin.id}
      plugin={plugin}
      path={path}
      active={active}
      onPath={onPath}
    />
  )
}

function InstalledPluginPage({
  plugin,
  path,
  active,
  onPath,
}: {
  plugin: InstalledPlugin
  path: string
  active: boolean
  onPath: (path: string) => void
}) {
  const navigate = useNavigate()
  const onScreen = useEffectEvent(() => active)
  const [title, setTitle] = useState<string | null>(null)
  useEffect(() => {
    if (!active) return
    const documentTitle = pageTitle(title ?? plugin.manifest.displayName)
    document.title = documentTitle
    return () => {
      if (document.title === documentTitle) document.title = pageTitle("Plugin")
    }
  }, [active, title, plugin.manifest.displayName])
  const agent = useAgentDock(plugin, onScreen)
  const wide = useMediaQuery("(min-width: 1100px)")
  const dock = agent.enabled && agent.open && (
    <PluginAgentDock
      plugin={plugin}
      chat={agent.chat}
      draft={agent.draft}
      tools={agent.tools}
      context={agent.context}
      active={active}
      onThread={agent.setThreadId}
    />
  )

  return (
    <main className="flex min-h-0 min-w-0 flex-1 flex-col">
      <header className="max-md:pl-space-12 flex h-10 shrink-0 items-center gap-space-2 border-b border-default px-space-4 text-xs">
        <PluginIcon
          icon={plugin.manifest.sidebar.icon}
          size={14}
          className="shrink-0 text-icon-secondary"
        />
        <span className="truncate font-medium text-primary">
          {plugin.manifest.displayName}
        </span>
        {title && <span className="truncate text-secondary">{title}</span>}
        <span className="ml-auto flex shrink-0 items-center gap-space-1 text-secondary max-sm:hidden">
          <ShieldCheckIcon size={14} />
          Sandboxed · #{plugin.version.number}
        </span>
        <Link
          to="/my-settings/plugins"
          className="shrink-0 text-secondary underline-offset-2 hover:text-primary hover:underline max-sm:ml-auto"
        >
          Manage
        </Link>
        {agent.enabled && agent.open && agent.threadId && (
          <Link
            to="/agents/$threadId"
            params={{ threadId: agent.threadId }}
            aria-label="Open the agent's chat as a full thread"
            title="Open as a full thread"
            className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-icon-secondary hover:bg-surface-level-1-hover"
          >
            <ArrowsOutIcon size={14} />
          </Link>
        )}
        {agent.enabled && (
          <Button
            size="sm"
            variant={agent.open ? "outlined" : "plain"}
            aria-pressed={agent.open}
            onClick={() => agent.setOpen(!agent.open)}
          >
            <ChatCircleIcon size={14} />
            Agent
          </Button>
        )}
      </header>
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          <PluginView
            plugin={plugin}
            path={path}
            active={active}
            onPath={(next, replace) => {
              onPath(next)
              if (onScreen())
                void navigate({
                  to: "/agents/plugins/$name/$",
                  params: { name: plugin.name, _splat: next.slice(1) },
                  replace,
                })
            }}
            setTitle={setTitle}
            agent={agent}
          />
        </div>
        {dock && wide && (
          <RightPanelShell
            mode="inline"
            widthStorageKey="open-swe.plugin-agent.width"
            defaultWidth={420}
          >
            {dock}
          </RightPanelShell>
        )}
      </div>
      {dock && !wide && (
        <SideSheet
          side="right"
          title="Agent"
          open
          onClose={() => agent.setOpen(false)}
          className={RIGHT_PANEL_SHEET_CLASS_NAME}
        >
          {dock}
        </SideSheet>
      )}
    </main>
  )
}

/**
 * The dock beside a plugin: whether it is open, which chat it shows, the
 * draft the plugin last put in its composer and the tools the plugin's frame
 * offers. A hidden plugin can choose its chat but not open the dock.
 */
function useAgentDock(plugin: InstalledPlugin, onScreen: () => boolean) {
  const enabled = plugin.manifest.permissions.includes("agent")
  const [open, setOpen] = useState(false)
  const [chat, setChat] = useState<ChatTarget>({
    name: "default",
    repository: null,
  })
  const [draft, setDraft] = useState<{ key: number; text: string }>()
  const [tools, setTools] = useState<ProvidedTools | null>(null)
  const [context, setContext] = useState<SharedContext | null>(null)
  const [threadId, setThreadId] = useState<string | null>(null)
  const latest = useRef<symbol | null>(null)
  return {
    enabled,
    open,
    setOpen,
    chat,
    draft,
    tools,
    context,
    threadId,
    setThreadId,
    shareContext: setContext,
    provideTools: (offer: AgentToolOffer, run: ToolRunner) => {
      const offered = Symbol("offer")
      latest.current = offered
      setTools({ offer, run })
      return () => {
        if (latest.current !== offered) return
        latest.current = null
        setTools(null)
      }
    },
    chooseChat: (next: ChatTarget) =>
      setChat((prev) =>
        prev.name === next.name && prev.repository === next.repository
          ? prev
          : next
      ),
    openChat: (text: string | null) => {
      if (!onScreen()) return
      setOpen(true)
      if (text) setDraft((prev) => ({ key: (prev?.key ?? 0) + 1, text }))
    },
  }
}
