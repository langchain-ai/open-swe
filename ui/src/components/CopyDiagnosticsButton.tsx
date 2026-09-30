import { CopyIcon } from "@phosphor-icons/react"
import { useState } from "react"

import { Button } from "@/components/ui/button"

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
    } catch {
      setCopyState("denied")
    }
  }

  return (
    <div className="flex items-center gap-2 pt-1">
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={() => void copy()}
      >
        <CopyIcon aria-hidden="true" className="size-3.5" />
        Copy diagnostics
      </Button>
      <span aria-live="polite" role="status">
        {copyState === "copied"
          ? "Diagnostics copied to clipboard."
          : copyState === "denied"
            ? "Clipboard unavailable. Check the browser's clipboard permission."
            : null}
      </span>
    </div>
  )
}
