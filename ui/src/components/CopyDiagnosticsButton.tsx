import { Button } from "@langchain/macaw-components/Button"
import { CopyIcon } from "@phosphor-icons/react/dist/ssr/Copy"
import { useState } from "react"

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
    <div className="flex items-center gap-space-2 pt-space-1">
      <Button
        color="secondary"
        variant="outlined"
        size="xs"
        leftDecorator={CopyIcon}
        onClick={() => void copy()}
      >
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
