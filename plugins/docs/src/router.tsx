import { createMemoryHistory, createRouter } from "@tanstack/react-router"

import { routeTree } from "./routeTree.gen"

// The frame's own URL is about:srcdoc, so the router keeps its location in
// memory; the dashboard's URL carries it, through `nav.setPath`.
export function getRouter() {
  return createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ["/"] }),
    defaultPreload: false,
    scrollRestoration: false,
  })
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof getRouter>
  }
}
