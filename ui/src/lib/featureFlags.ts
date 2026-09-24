import { useState } from "react"

import type { SessionUser } from "@/lib/api"
import { useHotkey } from "@/lib/hotkeys"
import { useIsHydrated } from "@/lib/hydration"

const STORAGE_KEY = "open-swe-feature-flags-panel"

function readVisible(): boolean {
  return (
    typeof window !== "undefined" &&
    window.localStorage.getItem(STORAGE_KEY) === "true"
  )
}

/** Whether the Feature Flags settings tab is shown; toggled with Mod+Shift+E. */
export function useFeatureFlagsPanel(user: SessionUser): boolean {
  const allowed = user.email?.toLowerCase().endsWith("@langchain.dev") ?? false
  const hydrated = useIsHydrated()
  const [visible, setVisible] = useState(readVisible)
  useHotkey(
    "mod+shift+e",
    () => {
      window.localStorage.setItem(STORAGE_KEY, String(!visible))
      setVisible(!visible)
    },
    { enabled: allowed }
  )
  return allowed && hydrated && visible
}
