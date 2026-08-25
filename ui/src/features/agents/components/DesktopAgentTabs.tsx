import { createContext, useContext, useEffect, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { HouseIcon, PlusIcon, XIcon } from "@phosphor-icons/react"
import { IoCloudOutline, IoDesktopOutline } from "react-icons/io5"

import type { DesktopLocalThreadSummary } from "@/desktop"
import type { AgentThread } from "@/features/agents/lib/types"
import { agentThreadKeys } from "@/features/agents/lib/queries"
import { localThreadKeys } from "@/features/agents/lib/desktopLocal"
import { cn } from "@/lib/utils"

export type DesktopAgentTab = {
  id: string
  source: "cloud" | "local"
  title: string
}
const STORAGE_KEY = "open-swe.desktop.tabs"

function readTabs(): Array<DesktopAgentTab> {
  if (typeof window === "undefined") return []
  try {
    const value: unknown = JSON.parse(
      window.localStorage.getItem(STORAGE_KEY) ?? "[]"
    )
    if (!Array.isArray(value)) return []
    return value.filter(
      (tab): tab is DesktopAgentTab =>
        typeof tab === "object" &&
        tab !== null &&
        typeof tab.id === "string" &&
        (tab.source === "cloud" || tab.source === "local") &&
        typeof tab.title === "string"
    )
  } catch {
    return []
  }
}

const DesktopAgentTabsContext = createContext<{
  open: (tab: DesktopAgentTab) => void
} | null>(null)
export function useDesktopAgentTabs() {
  return useContext(DesktopAgentTabsContext)
}

export function DesktopAgentTabsProvider({
  activeThreadId,
  activeLocalSessionId,
  children,
}: {
  activeThreadId?: string
  activeLocalSessionId?: string
  cloudEnabled: boolean
  children: React.ReactNode
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [tabs, setTabs] = useState(readTabs)
  const activeKey = activeLocalSessionId
    ? `local:${activeLocalSessionId}`
    : activeThreadId
      ? `cloud:${activeThreadId}`
      : null

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(tabs))
  }, [tabs])
  useEffect(() => {
    if (!activeKey) return
    const [source, id] = activeKey.split(":") as ["cloud" | "local", string]
    const cached =
      source === "cloud"
        ? queryClient.getQueryData<AgentThread>(agentThreadKeys.detail(id))
        : queryClient.getQueryData<DesktopLocalThreadSummary>(
            localThreadKeys.detail(id)
          )
    // oxlint-disable-next-line react/set-state-in-effect
    setTabs((current) =>
      current.some((tab) => `${tab.source}:${tab.id}` === activeKey)
        ? current
        : [...current, { id, source, title: cached?.title ?? "Thread" }]
    )
  }, [activeKey, queryClient])

  const open = (tab: DesktopAgentTab) =>
    setTabs((current) =>
      current.some((item) => item.id === tab.id && item.source === tab.source)
        ? current.map((item) =>
            item.id === tab.id && item.source === tab.source ? tab : item
          )
        : [...current, tab]
    )

  const close = (tab: DesktopAgentTab) => {
    const index = tabs.indexOf(tab)
    const next = tabs.filter((item) => item !== tab)
    setTabs(next)
    if (`${tab.source}:${tab.id}` !== activeKey) return
    const fallback = next[Math.min(index, next.length - 1)]
    if (!fallback) {
      void navigate({ to: "/agents" })
      return
    }
    void navigate(
      fallback.source === "local"
        ? { to: "/agents/local/$sessionId", params: { sessionId: fallback.id } }
        : { to: "/agents/$threadId", params: { threadId: fallback.id } }
    )
  }

  return (
    <DesktopAgentTabsContext.Provider value={{ open }}>
      <div className="flex h-svh flex-col overflow-hidden">
        <header
          data-desktop-drag-region=""
          className="flex h-9 shrink-0 items-center gap-1 border-b border-border bg-sidebar pr-2 pl-[90px]"
        >
          <Link
            to="/agents"
            aria-label="Home"
            className={cn(
              "flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground",
              !activeKey && "bg-accent text-foreground"
            )}
          >
            <HouseIcon className="size-4" />
          </Link>
          <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto py-1">
            {tabs.map((tab) => {
              const key = `${tab.source}:${tab.id}`
              const Icon =
                tab.source === "cloud" ? IoCloudOutline : IoDesktopOutline
              return (
                <div
                  key={key}
                  className={cn(
                    "group flex h-7 max-w-56 min-w-32 flex-1 items-center gap-1.5 rounded-md px-2 text-xs text-muted-foreground hover:bg-accent/70",
                    key === activeKey && "bg-accent text-foreground"
                  )}
                >
                  <Link
                    to={
                      tab.source === "local"
                        ? "/agents/local/$sessionId"
                        : "/agents/$threadId"
                    }
                    params={
                      tab.source === "local"
                        ? { sessionId: tab.id }
                        : { threadId: tab.id }
                    }
                    className="flex min-w-0 flex-1 items-center gap-1.5"
                  >
                    <Icon className="size-3.5 shrink-0" />
                    <span className="truncate">{tab.title}</span>
                  </Link>
                  <button
                    type="button"
                    aria-label={`Close ${tab.title}`}
                    onClick={() => close(tab)}
                    className="flex size-5 shrink-0 items-center justify-center rounded opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 hover:bg-background"
                  >
                    <XIcon className="size-3" />
                  </button>
                </div>
              )
            })}
          </div>
          <Link
            to="/agents"
            onClick={() =>
              window.sessionStorage.setItem("open-swe.desktop.new-thread", "1")
            }
            aria-label="New thread"
            className="flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <PlusIcon className="size-4" />
          </Link>
        </header>
        <div className="flex min-h-0 flex-1">{children}</div>
      </div>
    </DesktopAgentTabsContext.Provider>
  )
}
