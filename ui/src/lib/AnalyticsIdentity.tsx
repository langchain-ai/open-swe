import { useEffect } from "react"
import { productAnalytics } from "./posthog"
import { useSession } from "./session"

export function AnalyticsIdentity() {
  const { data: session } = useSession()

  useEffect(() => {
    if (session) productAnalytics.identify(session)
  }, [session])

  return null
}
