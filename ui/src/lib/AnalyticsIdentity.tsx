import { useEffect } from "react"
import { useRouterState } from "@tanstack/react-router"
import { productAnalytics } from "./analytics"
import { useSession } from "./session"

export function AnalyticsIdentity() {
  const { data: session } = useSession()

  const pathname = useRouterState({
    select: (state) => state.matches.at(-1)?.pathname ?? "/",
  })
  const route = useRouterState({
    select: (state) => state.matches.at(-1)?.fullPath ?? "/unknown",
  })

  useEffect(() => {
    if (session === undefined) return
    if (session) productAnalytics.identify(session)
    else productAnalytics.reset()
  }, [session])

  const sessionReady = session !== undefined
  useEffect(() => {
    if (sessionReady) productAnalytics.page(pathname, route)
  }, [sessionReady, pathname, route])

  return null
}
