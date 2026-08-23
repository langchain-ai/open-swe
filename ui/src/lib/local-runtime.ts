import { createIsomorphicFn } from "@tanstack/react-start"

/**
 * Whether this server is a local runtime, which is what decides if the page
 * offers local mode. A hosted deployment has no graph to run against.
 *
 * The server answers from its environment and stamps a global into the
 * document; the client reads that same global back, so the render matches.
 */
export const localRuntimeScript = createIsomorphicFn()
  .client(
    () => typeof window !== "undefined" && window.__OPEN_SWE_LOCAL__ === true
  )
  .server(() => Boolean(process.env.OPEN_SWE_LOCAL_GRAPH_ORIGIN))
