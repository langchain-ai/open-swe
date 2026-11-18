import { ChatState } from "@langchain/macaw-components/ChatState"

export function ThinkingSpinner({
  isActive,
  settingUpSandbox = false,
  label,
  since,
}: {
  isActive: boolean
  settingUpSandbox?: boolean
  label?: string
  since?: number
}) {
  if (!isActive) return null
  return (
    <ChatState
      className="my-space-2"
      status="streaming"
      label={
        settingUpSandbox
          ? "Agent is setting up the environment…"
          : (label ?? "Working…")
      }
      showElapsed
      startedAt={since}
    />
  )
}
