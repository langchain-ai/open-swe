/// <reference types="vite/client" />
import type { PluginHost } from "@openswe/plugin-sdk"
import {
  PluginHostProvider,
  useDashboardTheme,
} from "@openswe/plugin-sdk/react"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  HeadContent,
  Outlet,
  Scripts,
  createRootRoute,
  useRouter,
} from "@tanstack/react-router"
import { useEffect, useState, type ReactNode } from "react"

import { hostConnection } from "@/lib/host"
import styles from "@/styles.css?url"

export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "Docs" },
    ],
    links: [{ rel: "stylesheet", href: styles }],
  }),
  shellComponent: Shell,
  component: Root,
})

function Shell({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <HeadContent />
      </head>
      <body className="bg-surface-level-0 text-primary antialiased">
        {children}
        <Scripts />
      </body>
    </html>
  )
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false },
  },
})

/** Waits for the dashboard's port, then keeps the router on the dashboard's URL. */
function Root() {
  const [host, setHost] = useState<PluginHost | null>(null)
  useEffect(() => {
    void hostConnection().then(setHost)
  }, [])
  if (!host)
    return (
      <div className="flex h-svh items-center justify-center">
        <Spinner />
      </div>
    )
  return (
    <PluginHostProvider host={host}>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <PathSync host={host} />
          <Themed />
        </TooltipProvider>
      </QueryClientProvider>
    </PluginHostProvider>
  )
}

function Themed() {
  useDashboardTheme()
  return <Outlet />
}

function PathSync({ host }: { host: PluginHost }) {
  const router = useRouter()
  useEffect(() => {
    router.history.replace(host.context.path)
    let path = host.context.path
    const offHost = host.on((event) => {
      if (event.type !== "path") return
      path = event.path
      if (event.path !== router.state.location.href)
        router.history.replace(event.path)
    })
    const offRouter = router.history.subscribe(({ location, action }) => {
      if (location.href === path) return
      path = location.href
      void host.nav.setPath(path, { replace: action.type !== "PUSH" })
    })
    return () => {
      offHost()
      offRouter()
    }
  }, [host, router])
  return null
}
