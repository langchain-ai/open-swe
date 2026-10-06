import { Inline } from "@langchain/gtm-platform-design-system/ui/box"

/** The thread's one working line: the words say what, the shimmer says it is live. */
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
    <Inline
      align="center"
      gap="sm"
      className="min-h-5"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <span className="shimmer-text text-label">
        {settingUpSandbox
          ? "Agent is setting up the environment…"
          : (label ?? "Working…")}
      </span>
    </Inline>
  )
}
