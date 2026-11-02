import { toast } from "sonner"

export function copyText(text: string, copied: string): void {
  reportCopy(navigator.clipboard.writeText(text), copied)
}

export function copyPageLink(): void {
  reportCopy(
    window.openSweDesktop
      ? window.openSweDesktop.copyPageLink()
      : navigator.clipboard.writeText(window.location.href),
    "Copied the link"
  )
}

function reportCopy(pending: Promise<void>, copied: string): void {
  pending.then(
    () => toast.success(copied),
    (error: unknown) => {
      console.warn("Could not copy to the clipboard", { error })
      toast.error("Couldn't copy to the clipboard")
    }
  )
}
