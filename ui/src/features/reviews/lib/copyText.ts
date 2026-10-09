import { toast } from "sonner"

/** Copies `text` and says so; a refused clipboard says that instead of failing silently. */
export function copyText(text: string, copied: string): void {
  navigator.clipboard.writeText(text).then(
    () => toast.success(copied),
    (error: unknown) => {
      console.warn("Could not copy to the clipboard", { error })
      toast.error("Couldn't copy to the clipboard")
    }
  )
}
