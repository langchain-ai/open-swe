import { Badge } from "@langchain/macaw-components/Badge"

import { statusColors } from "../lib/status"

export function StatusPill({ status }: { status: string }) {
  return (
    <Badge size="sm" color={statusColors[status] ?? "secondary"}>
      {status}
    </Badge>
  )
}
