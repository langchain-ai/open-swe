import { useCallback, useRef } from "react"
import { useBlocker } from "@tanstack/react-router"

import { useConfirm } from "@/components/ConfirmDialog"

export function useUnsavedChangesWarning(isDirty: boolean) {
  const confirm = useConfirm()
  const allowNavigationRef = useRef(false)
  const shouldBlockFn = useCallback(async () => {
    if (allowNavigationRef.current) return false
    return !(await confirm({
      title: "Leave without saving?",
      description: "You have unsaved changes.",
      confirmLabel: "Leave",
      cancelLabel: "Keep editing",
      destructive: true,
    }))
  }, [confirm])

  useBlocker({
    shouldBlockFn,
    enableBeforeUnload: isDirty,
    disabled: !isDirty,
  })

  return useCallback(() => {
    allowNavigationRef.current = true
  }, [])
}
