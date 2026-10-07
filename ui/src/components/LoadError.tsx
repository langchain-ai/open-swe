import { useEffect, useState } from "react"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"

import { AlertTriangle, RotateCcw } from "@/components/glyphs"

/** A page or panel that could not load: what failed, the details to share, and the way back. */
export function LoadError({
  error,
  title = "Something went wrong",
  context,
  retry = () => window.location.reload(),
}: {
  error: unknown
  title?: string
  context?: string
  retry?: () => void
}) {
  const details = [
    context,
    error instanceof Error ? `${error.name}: ${error.message}` : String(error),
  ]
    .filter(Boolean)
    .join("\n")

  useEffect(() => {
    console.error("UI load failed", { context, error })
  }, [context, error])

  return (
    <Stack grow justify="center" align="center" className="min-w-0 p-6">
      <Box className="w-full max-w-reading">
        <StateNotice
          tone="RISK"
          icon={AlertTriangle}
          title={title}
          description="Try again. If this keeps happening, share the page URL and the details below with your workspace admin."
          action={
            <Stack gap="md">
              <Box
                render={<pre />}
                radius="badge"
                border="line"
                bg="panel"
                padding="md"
                className="max-h-48 overflow-auto font-mono text-meta break-all whitespace-pre-wrap text-ink"
              >
                {details}
              </Box>
              <Inline gap="sm">
                <Button size="compact" onClick={retry}>
                  <Icon icon={RotateCcw} size="sm" />
                  Try again
                </Button>
                <Button
                  size="compact"
                  variant="ghost"
                  onClick={() =>
                    window.location.assign(import.meta.env.BASE_URL)
                  }
                >
                  Back to home
                </Button>
              </Inline>
            </Stack>
          }
        />
      </Box>
    </Stack>
  )
}

export function useLoadTimedOut(loading: boolean) {
  const [timedOut, setTimedOut] = useState(false)
  useEffect(() => {
    const timer = window.setTimeout(
      () => setTimedOut(loading),
      loading ? 30_000 : 0
    )
    return () => window.clearTimeout(timer)
  }, [loading])
  return loading && timedOut
}
