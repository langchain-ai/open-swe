import { usePluginHost } from "@openswe/plugin-sdk/react"
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type AnchorHTMLAttributes,
  type ReactNode,
} from "react"

import { parseRoute, routePath, type InboxRoute, type Route } from "./route"

interface Router {
  path: string
  route: Route
  /** The inbox tab and page last shown, which going back to the inbox returns to. */
  inbox: InboxRoute
  navigate: (route: Route) => void
}

const RouterContext = createContext<Router | null>(null)

/**
 * Keeps the app's location in the web UI's URL: it starts at the host's path,
 * follows back and forward there, and reports each move the app makes.
 */
export function RouterProvider({ children }: { children: ReactNode }) {
  const host = usePluginHost()
  const [path, setPath] = useState(host.context.path)
  const route = parseRoute(path)
  const [inbox, setInbox] = useState<InboxRoute>(
    route.page === "inbox"
      ? route
      : { page: "inbox", scope: "review-requested", index: 0 }
  )
  if (
    route.page === "inbox" &&
    (route.scope !== inbox.scope ||
      route.index !== inbox.index ||
      route.person !== inbox.person)
  )
    setInbox(route)
  useEffect(
    () =>
      host.on((event) => {
        if (event.type === "path") setPath(event.path)
      }),
    [host]
  )
  const navigate = (to: Route) => {
    const next = routePath(to)
    if (next === path) return
    setPath(next)
    void host.nav.setPath(next)
  }
  return (
    <RouterContext value={{ path, route, inbox, navigate }}>
      {children}
    </RouterContext>
  )
}

export function useRouter(): Router {
  const router = useContext(RouterContext)
  if (!router) throw new Error("useRouter needs a RouterProvider")
  return router
}

/** A link to a page of the app; the frame cannot navigate, so the router moves instead. */
export function RouteLink({
  to,
  ...props
}: { to: Route } & Omit<
  AnchorHTMLAttributes<HTMLAnchorElement>,
  "href" | "onClick"
>) {
  const { navigate } = useRouter()
  return (
    <a
      {...props}
      href={routePath(to)}
      onClick={(event) => {
        event.preventDefault()
        navigate(to)
      }}
    />
  )
}
