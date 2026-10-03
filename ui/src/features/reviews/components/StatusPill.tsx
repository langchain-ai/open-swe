import { Badge } from "@/components/ui/badge"
import { statusVariants } from "../lib/status"

export function StatusPill({ status }: { status: string }) {
  return <Badge variant={statusVariants[status] ?? "muted"}>{status}</Badge>
}
