import { useState } from "react"
import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { cn } from "@langchain/gtm-platform-design-system/ui/cn"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"

import { Check, Copy } from "@/components/glyphs"

export function CopyDiagnosticsButton({
  getDiagnostics,
}: {
  getDiagnostics: () => object
}) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "denied">(
    "idle"
  )
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(
        JSON.stringify(getDiagnostics(), null, 2)
      )
      setCopyState("copied")
    } catch (error) {
      console.warn("Diagnostics copy failed", { error })
      setCopyState("denied")
    }
  }

  return (
    <Inline gap="sm" align="center" className="pt-1">
      <Button
        type="button"
        size="compact"
        variant="outline"
        onClick={() => void copy()}
      >
        <Icon icon={copyState === "copied" ? Check : Copy} size="sm" />
        Copy diagnostics
      </Button>
      <Box
        render={<span />}
        aria-live="polite"
        role="status"
        className={cn(
          "text-meta",
          copyState === "denied" ? "text-risk" : "text-ink-subtle"
        )}
      >
        {copyState === "copied"
          ? "Diagnostics copied to clipboard."
          : copyState === "denied"
            ? "Clipboard unavailable. Check the browser's clipboard permission."
            : null}
      </Box>
    </Inline>
  )
}
