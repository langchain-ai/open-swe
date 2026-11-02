import { Spinner } from "@langchain/macaw-components/Spinner"
import { connect, type PluginHost } from "@openswe/plugin-sdk"
import { PluginHostProvider } from "@openswe/plugin-sdk/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { StrictMode, useEffect, useState } from "react"
import { createRoot } from "react-dom/client"

import { App } from "./App"
import { retryOnce } from "./lib/queries"
import { RouterProvider } from "./lib/router"
import "./styles.css"

// Listen as the module loads: the web UI hands over its port on the frame's
// load event, before React has mounted anything.
const pending = connect()

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: retryOnce,
      refetchOnWindowFocus: false,
    },
  },
})

function Root() {
  const [host, setHost] = useState<PluginHost | null>(null)
  useEffect(() => {
    void pending.then(setHost)
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
        <RouterProvider>
          <App />
        </RouterProvider>
      </QueryClientProvider>
    </PluginHostProvider>
  )
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>
)
