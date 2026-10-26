import { useEffect } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useRouter } from "@tanstack/react-router"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"

import { normalizeBuildInfo } from "@/lib/api"
import { reloadForBuild } from "@/lib/buildReload"
import { useIsHydrated } from "@/lib/hydration"
import { sessionQueryOptions } from "@/lib/session"

export function VersionMismatchBanner() {
  const hydrated = useIsHydrated()
  const router = useRouter()
  const queryClient = useQueryClient()
  // `InvalidationStream` refetches the session when a stream reports another backend.
  const { data: user } = useQuery(sessionQueryOptions)
  // The desktop app serves its own bundled UI, so a reload cannot pick up a deploy.
  const browser = hydrated && !window.openSweDesktop
  const running = browser ? window.__OPEN_SWE_BUNDLE__?.commit : null
  const deployed = normalizeBuildInfo(user?.build_info)?.backend.commit
  const shown = !!running && !!deployed && running !== deployed

  // `styles.css` makes room for the banner under this class.
  useEffect(() => {
    if (!shown) return
    document.body.classList.add("version-mismatch")
    return () => document.body.classList.remove("version-mismatch")
  }, [shown])

  // Moving to another page is the least disruptive moment to load the new
  // build. `onLoad` is the first event after the router commits the URL, so
  // the reload lands on the destination; earlier ones still see the old page.
  useEffect(() => {
    if (!shown || !deployed) return
    return router.subscribe("onLoad", (event) => {
      if (event.fromLocation && event.pathChanged)
        reloadForBuild(deployed, queryClient)
    })
  }, [shown, deployed, router, queryClient])

  // A deploy removes the previous build's lazy chunks before the session notices it.
  useEffect(() => {
    if (!browser || !running) return
    const onPreloadError = (event: Event) => {
      if (reloadForBuild(`chunks-missing:${running}`, queryClient))
        event.preventDefault()
    }
    window.addEventListener("vite:preloadError", onPreloadError)
    return () => window.removeEventListener("vite:preloadError", onPreloadError)
  }, [browser, running, queryClient])

  if (!shown) return null

  return (
    <div role="status" className="shrink-0">
      <Banner
        flush
        intent="warning"
        className="h-10 border-b border-b-warning py-0 text-xs"
        action={
          <Button
            size="xs"
            variant="outlined"
            color="secondary"
            onClick={() => window.location.reload()}
          >
            Reload
          </Button>
        }
      >
        <span className="truncate">
          New changes are available. Reload to get the latest updates.
        </span>
      </Banner>
    </div>
  )
}
