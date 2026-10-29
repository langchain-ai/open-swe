/**
 * React bindings: the host in context, its live context as state, and the
 * dashboard's appearance applied to this frame.
 */
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react"

import type { PluginHost } from "./client"
import type { HostContext, Theme } from "./protocol"

const HostContextValue = createContext<PluginHost | null>(null)

export function PluginHostProvider({
  host,
  children,
}: {
  host: PluginHost
  children: ReactNode
}) {
  return <HostContextValue value={host}>{children}</HostContextValue>
}

export function usePluginHost(): PluginHost {
  const host = useContext(HostContextValue)
  if (!host) throw new Error("usePluginHost needs a PluginHostProvider")
  return host
}

/** The dashboard's context, re-rendered as its theme and path change. */
export function useHostContext(): HostContext {
  const host = usePluginHost()
  const [context, setContext] = useState(host.context)
  useEffect(() => host.on(() => setContext(host.context)), [host])
  return context
}

/**
 * Matches the dashboard's light or dark mode: Macaw reads `html.dark`, and
 * `color-scheme` styles the browser's own controls and scrollbars.
 */
export function applyTheme(theme: Theme, root = document.documentElement) {
  root.classList.toggle("dark", theme === "dark")
  root.dataset.theme = theme
  root.style.colorScheme = theme
}

/** Keeps this frame's theme on the dashboard's for as long as it is mounted. */
export function useDashboardTheme(): Theme {
  const { theme } = useHostContext()
  useEffect(() => applyTheme(theme), [theme])
  return theme
}
