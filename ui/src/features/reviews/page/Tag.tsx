import { Badge } from "@langchain/macaw-components/Badge"

/** A small label beside a name or path: "bot", "Outdated", "Added". */
export function Tag({
  className,
  title,
  children,
}: {
  className?: string
  title?: string
  children: string
}) {
  return (
    <Badge
      size="xxs"
      color="secondary"
      rounded="xs"
      className={className}
      title={title}
    >
      {children}
    </Badge>
  )
}
