import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"

import { statusTones } from "../lib/status"

export function StatusPill({ status }: { status: string }) {
  const tone = statusTones[status] ?? "neutral"
  return (
    <Badge tier="quiet" tone={tone} dot={status === "Pending"}>
      {status}
    </Badge>
  )
}
