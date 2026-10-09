import { ThinkingState } from "@langchain/macaw-components/ThinkingState"

export function ThinkingSpinner({
  isActive,
  settingUpSandbox = false,
  label,
}: {
  isActive: boolean
  settingUpSandbox?: boolean
  label?: string
}) {
  if (!isActive) return null

  return (
    <ThinkingState
      className="my-2"
      label={
        settingUpSandbox
          ? "Agent is setting up the environment…"
          : (label ?? "Working…")
      }
    />
  )
}
