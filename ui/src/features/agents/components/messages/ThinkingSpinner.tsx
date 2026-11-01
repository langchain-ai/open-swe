import { LoadingIndicator } from "@langchain/macaw-components/ThinkingState"

import { ElapsedSeconds } from "./ElapsedSeconds"

export function ThinkingSpinner({
  isActive,
  settingUpSandbox = false,
  label,
  since,
}: {
  isActive: boolean
  settingUpSandbox?: boolean
  label?: string
  /** When the work began, epoch ms; defaults to when the spinner appears. */
  since?: number
}) {
  if (!isActive) return null

  return (
    <div
      className="my-space-2 flex items-center gap-space-2"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <LoadingIndicator className="size-3" />
      <span className="shimmer-text text-xs">
        {settingUpSandbox
          ? "Agent is setting up the environment…"
          : (label ?? "Working…")}
      </span>
      <ElapsedSeconds since={since} />
    </div>
  )
}
