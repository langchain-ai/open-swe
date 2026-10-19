import { useQuery } from "@tanstack/react-query"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"

import { normalizeBuildInfo } from "@/lib/api"
import { useIsHydrated } from "@/lib/hydration"
import { sessionQueryOptions } from "@/lib/session"

export function VersionMismatchBanner() {
  const hydrated = useIsHydrated()
  // `InvalidationStream` refetches the session when a stream reports another backend.
  const { data: user } = useQuery(sessionQueryOptions)
  const running = hydrated ? window.__OPEN_SWE_BUNDLE__?.commit : null
  const deployed = normalizeBuildInfo(user?.build_info)?.backend.commit
  if (!running || !deployed || running === deployed || window.openSweDesktop)
    return null

  return (
    <div role="status" className="version-mismatch-banner shrink-0">
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
