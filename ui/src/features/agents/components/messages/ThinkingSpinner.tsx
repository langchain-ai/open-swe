import { LiveActivityStatus } from "./timeline/LiveActivityStatus"

export function ThinkingSpinner({
  isActive,
  label,
  since,
}: {
  isActive: boolean
  label?: string
  since?: number
}) {
  if (!isActive) return null
  return (
    <LiveActivityStatus
      className="my-space-2"
      label={label ?? "Thinking…"}
      since={since}
      announce
    />
  )
}
