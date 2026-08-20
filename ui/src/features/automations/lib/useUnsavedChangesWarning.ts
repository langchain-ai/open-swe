import { useCallback, useRef } from "react"
import { useBlocker } from "@tanstack/react-router"

const UNSAVED_CHANGES_MESSAGE =
  "You have unsaved changes. Leave without saving?"

export function useUnsavedChangesWarning(isDirty: boolean) {
  const allowNavigationRef = useRef(false)
  const shouldBlockFn = useCallback(() => {
    if (allowNavigationRef.current) return false
    return !window.confirm(UNSAVED_CHANGES_MESSAGE)
  }, [])

  useBlocker({
    shouldBlockFn,
    enableBeforeUnload: isDirty,
    disabled: !isDirty,
  })

  return useCallback(() => {
    allowNavigationRef.current = true
  }, [])
}
