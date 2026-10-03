import { cn } from "@/lib/utils"

export function StatusPill({ connected }: { connected: boolean }) {
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-[10px] font-medium",
        connected
          ? "bg-primary/10 text-primary"
          : "bg-muted text-muted-foreground"
      )}
    >
      {connected ? "Connected" : "Not connected"}
    </span>
  )
}
