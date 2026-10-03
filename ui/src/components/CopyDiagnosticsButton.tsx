import { CopyIcon } from "@phosphor-icons/react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { useCopyToClipboard } from "@/lib/useCopyToClipboard"

export function CopyDiagnosticsButton({
  getDiagnostics,
}: {
  getDiagnostics: () => object
}) {
  const { copied, copy } = useCopyToClipboard()
  const [denied, setDenied] = useState(false)
  const copyDiagnostics = async () => {
    setDenied(!(await copy(JSON.stringify(getDiagnostics(), null, 2))))
  }

  return (
    <div className="flex items-center gap-2 pt-1">
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={() => void copyDiagnostics()}
      >
        <CopyIcon aria-hidden="true" className="size-3.5" />
        Copy diagnostics
      </Button>
      <span aria-live="polite" role="status">
        {copied
          ? "Diagnostics copied to clipboard."
          : denied
            ? "Clipboard unavailable. Check the browser's clipboard permission."
            : null}
      </span>
    </div>
  )
}
