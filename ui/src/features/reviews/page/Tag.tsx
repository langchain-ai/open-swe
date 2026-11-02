import { Badge } from "@langchain/macaw-components/Badge"
import type { ComponentProps } from "react"

/** A small label beside a name or path: "bot", "Outdated", "Added". */
export function Tag({
  className,
  title,
  color = "secondary",
  children,
}: {
  className?: string
  title?: string
  color?: ComponentProps<typeof Badge>["color"]
  children: string
}) {
  return (
    <Badge
      size="xxs"
      color={color}
      rounded="xs"
      className={className}
      title={title}
    >
      {children}
    </Badge>
  )
}
