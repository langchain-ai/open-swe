import { Kbd } from "@langchain/macaw-components/Kbd"

import { useShortcutLabel } from "@/lib/hotkeys"

export function SubmitShortcut() {
  const shortcut = useShortcutLabel("mod+enter")
  return (
    <Kbd variant="inherit" aria-hidden="true" className="opacity-80">
      {shortcut.replace("Enter", "↵")}
    </Kbd>
  )
}
