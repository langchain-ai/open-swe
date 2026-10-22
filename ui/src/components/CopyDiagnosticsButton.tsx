import { Button } from "@langchain/macaw-components/Button"
import { CopyIcon } from "@phosphor-icons/react/dist/ssr/Copy"
import { toast } from "sonner"

export function CopyDiagnosticsButton({
  getDiagnostics,
}: {
  getDiagnostics: () => object
}) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(
        JSON.stringify(getDiagnostics(), null, 2)
      )
      toast.success("Diagnostics copied to clipboard.")
    } catch {
      toast.error(
        "Clipboard unavailable. Check the browser's clipboard permission."
      )
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
    </div>
  )
}
