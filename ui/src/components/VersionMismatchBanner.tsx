import { Link } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"

import { AlertTriangle } from "@/components/glyphs"
import { normalizeBuildInfo } from "@/lib/api"
import { useIsHydrated } from "@/lib/hydration"
import { sessionQueryOptions } from "@/lib/session"

const BANNER_LINK_CLASS =
  "shrink-0 font-medium underline underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-primary"

/*
 * An app-wide strip rather than an Alert panel: it sits above every shell at a
 * fixed 40px (`h-row-data`), which `styles.css` subtracts from `h-svh`.
 */
export function VersionMismatchBanner() {
  const hydrated = useIsHydrated()
  // `InvalidationStream` refetches the session when a stream reports another backend.
  const { data: user } = useQuery(sessionQueryOptions)
  const running = hydrated ? window.__OPEN_SWE_BUNDLE__?.commit : null
  const deployed = normalizeBuildInfo(user?.build_info)?.backend.commit
  if (!running || !deployed || running === deployed || window.openSweDesktop)
    return null

  return (
    <Inline
      role="status"
      gap="sm"
      align="center"
      justify="center"
      className="version-mismatch-banner h-row-data shrink-0 border-b border-attention/20 bg-attention-bg px-3 text-label text-attention"
    >
      <Icon icon={AlertTriangle} size="sm" />
      <Box render={<span />} className="truncate">
        Frontend version differs from the deployed backend.
      </Box>
      <Link to="/my-settings/about" className={BANNER_LINK_CLASS}>
        Details
      </Link>
      <Box
        render={<button type="button" />}
        onClick={() => window.location.reload()}
        className={BANNER_LINK_CLASS}
      >
        Reload
      </Box>
    </Inline>
  )
}
