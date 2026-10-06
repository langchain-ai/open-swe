import { Link } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"

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
    <div
      role="status"
      className="version-mismatch-banner flex h-10 shrink-0 items-center justify-center gap-2 border-b border-amber-300 bg-amber-100 px-3 text-xs text-amber-950 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100"
    >
      <span className="truncate">
        Frontend version differs from the deployed backend.
      </span>
      <Link
        to="/my-settings"
        hash="about"
        className="shrink-0 underline underline-offset-2"
      >
        Details
      </Link>
      <button
        type="button"
        onClick={() => window.location.reload()}
        className="shrink-0 font-medium underline underline-offset-2"
      >
        Reload
      </button>
    </div>
  )
}
