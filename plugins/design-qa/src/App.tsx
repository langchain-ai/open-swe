import { useDashboardTheme } from "@openswe/plugin-sdk/react"
import { useEffect } from "react"

import { InboxPage } from "./components/InboxPage"
import { PullRequestPage } from "./components/PullRequestPage"
import { pullRequestLabel } from "./lib/pullRequest"
import { useRouter } from "./lib/router"

export function App() {
  useDashboardTheme()
  const { path, route } = useRouter()
  // Newer browsers return a promise from scrollTo, which React would take for a cleanup.
  useEffect(() => {
    window.scrollTo({ top: 0 })
  }, [path])
  if (route.page === "inbox") return <InboxPage />
  return (
    <PullRequestPage
      key={pullRequestLabel(route.pr)}
      pr={route.pr}
      runId={route.run}
    />
  )
}
