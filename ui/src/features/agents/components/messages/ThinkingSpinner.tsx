export function ThinkingSpinner({
  isActive,
  settingUpSandbox = false,
  label,
  onRetry,
}: {
  isActive: boolean
  settingUpSandbox?: boolean
  label?: string
  onRetry?: () => void
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
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
        >
          Retry now
        </button>
      )}
    </div>
  )
}
