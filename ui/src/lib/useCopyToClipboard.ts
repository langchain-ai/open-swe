import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"

const COPIED_RESET_MS = 1500

/** `navigator.clipboard` is missing outside secure contexts (plain-http tunnels) and can reject on permissions, so fall back to execCommand. */
async function writeClipboard(text: string): Promise<void> {
  if (window.openSweDesktop) {
    await window.openSweDesktop.writeClipboard(text)
    return
  }
  if (typeof navigator.clipboard?.writeText === "function") {
    try {
      await navigator.clipboard.writeText(text)
      return
    } catch (error) {
      console.warn("Clipboard API write failed, falling back", error)
    }
  }
  execCommandCopy(text)
}

function execCommandCopy(text: string): void {
  const textarea = document.createElement("textarea")
  textarea.value = text
  textarea.setAttribute("readonly", "")
  textarea.style.position = "fixed"
  textarea.style.top = "-9999px"
  document.body.appendChild(textarea)
  try {
    textarea.select()
    textarea.setSelectionRange(0, text.length)
    if (!document.execCommand("copy"))
      throw new Error("execCommand copy failed")
  } finally {
    document.body.removeChild(textarea)
  }
}

export function useCopyToClipboard(): {
  copied: boolean
  copy: (text: string) => Promise<boolean>
} {
  const [copied, setCopied] = useState(false)
  const resetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(
    () => () => {
      if (resetTimerRef.current) clearTimeout(resetTimerRef.current)
    },
    []
  )

  const copy = useCallback(async (text: string) => {
    try {
      await writeClipboard(text)
    } catch (error) {
      console.error("Clipboard write failed", error)
      toast.error("Couldn't copy to the clipboard")
      return false
    }
    setCopied(true)
    if (resetTimerRef.current) clearTimeout(resetTimerRef.current)
    resetTimerRef.current = setTimeout(() => setCopied(false), COPIED_RESET_MS)
    return true
  }, [])

  return { copied, copy }
}
