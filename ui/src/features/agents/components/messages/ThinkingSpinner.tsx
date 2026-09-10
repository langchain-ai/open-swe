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
      className="my-2 flex items-center gap-2"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <span className="shimmer-text text-xs">
        {settingUpSandbox
          ? "Agent is setting up the environment…"
          : (label ?? "Working…")}
      </span>
    </div>
  )
}
