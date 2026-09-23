import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"

const COPIED_RESET_MS = 1500

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
      await navigator.clipboard.writeText(text)
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
