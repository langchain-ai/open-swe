import { cn } from "@/lib/utils"
import { statusTones } from "../lib/status"

export function StatusPill({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-label font-medium",
        statusTones[status] ?? "border-line bg-muted text-ink-subtle"
      )}
    >
      {status}
    </span>
  )
}
