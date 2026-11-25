import { Kbd } from "@langchain/macaw-components/Kbd"

import { useShortcutLabel } from "@/lib/hotkeys"

/** ⌘↵ on a Mac, Ctrl ↵ elsewhere. */
export function useSubmitShortcutLabel() {
  return useShortcutLabel("mod+enter").replace("Enter", "↵")
}

export function SubmitShortcut() {
  const shortcut = useSubmitShortcutLabel()
  return (
    <Kbd variant="inherit" aria-hidden="true" className="opacity-80">
      {shortcut}
    </Kbd>
  )
}
