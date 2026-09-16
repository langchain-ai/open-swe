import { createRouter as createTanStackRouter } from "@tanstack/react-router"
import { setupRouterSsrQueryIntegration } from "@tanstack/react-router-ssr-query"

import { onRouterNavigation } from "./lib/perf/threadLoad"
import { makeQueryClient } from "./lib/query"
import { routeTree } from "./routeTree.gen"
import { LoadError } from "./components/LoadError"

export function getRouter() {
  const queryClient = makeQueryClient()
  const router = createTanStackRouter({
    routeTree,
    context: { queryClient },
    // Vite's `base`, so a build made for a mount prefix routes under it.
    basepath: import.meta.env.BASE_URL,

    scrollRestoration: true,
    defaultPreload: "intent",
    defaultErrorComponent: ({ error }) => <LoadError error={error} />,
    defaultPreloadStaleTime: 0,
  })

  setupRouterSsrQueryIntegration({ router, queryClient })

  // The thread-load clock starts at the navigation, not at the route mount.
  router.subscribe("onBeforeNavigate", (event) =>
    onRouterNavigation(event.toLocation.pathname, event.fromLocation?.pathname)
  )

  return router
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof getRouter>
  }
}
