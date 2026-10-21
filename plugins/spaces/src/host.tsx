import type { HostContext, PluginHost } from "@openswe/plugin-sdk"
import { HostError } from "@openswe/plugin-sdk"
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react"

const HostContextValue = createContext<PluginHost | null>(null)

export function HostProvider({
  host,
  children,
}: {
  host: PluginHost
  children: ReactNode
}) {
  return <HostContextValue value={host}>{children}</HostContextValue>
}

export function useHost(): PluginHost {
  const host = useContext(HostContextValue)
  if (!host) throw new Error("useHost outside HostProvider")
  return host
}

/** The dashboard's context, kept current as the theme and path change. */
export function useHostContext(): HostContext {
  const host = useHost()
  const [context, setContext] = useState(host.context)
  useEffect(
    () =>
      host.on(() => {
        setContext(host.context)
      }),
    [host]
  )
  return context
}

export function errorMessage(error: unknown): string {
  if (error instanceof HostError) return error.message
  return error instanceof Error ? error.message : String(error)
}
