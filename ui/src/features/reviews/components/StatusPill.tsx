import { Badge } from "@langchain/macaw-components/Badge"

import { statusColors } from "../lib/status"

export function StatusPill({
  status,
  size = "sm",
}: {
  status: string
  size?: "xs" | "sm"
}) {
  return (
    <Badge size={size} color={statusColors[status] ?? "secondary"}>
      {status}
    </Badge>
  )
}
