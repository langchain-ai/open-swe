import { LoadingIndicator } from "@langchain/macaw-components/ThinkingState"

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
    <div
      className="my-space-2 flex items-center gap-space-2"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <LoadingIndicator className="size-3" speed="slow" />
      <span className="shimmer-text text-xs">
        {settingUpSandbox
          ? "Agent is setting up the environment…"
          : (label ?? "Working…")}
      </span>
    </div>
  )
}
