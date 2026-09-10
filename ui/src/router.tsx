import { createRouter as createTanStackRouter } from "@tanstack/react-router"
import { setupRouterSsrQueryIntegration } from "@tanstack/react-router-ssr-query"

import { makeQueryClient } from "./lib/query"
import { routeTree } from "./routeTree.gen"

export function getRouter() {
  const queryClient = makeQueryClient()
  const router = createTanStackRouter({
    routeTree,
    context: { queryClient },
    // Vite's `base`, so a build made for a mount prefix routes under it.
    basepath: import.meta.env.BASE_URL,

    scrollRestoration: true,
    defaultPreload: "intent",
    defaultPreloadStaleTime: 0,
  })

  setupRouterSsrQueryIntegration({ router, queryClient })

  return router
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof getRouter>
  }
}
