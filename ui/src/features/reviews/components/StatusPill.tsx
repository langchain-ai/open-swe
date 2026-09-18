import { cn } from "@/lib/utils"
import { statusTones } from "../lib/status"

export function StatusPill({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium",
        statusTones[status] ?? "border-border bg-muted text-muted-foreground"
      )}
    >
      {status}
    </span>
  )
}
