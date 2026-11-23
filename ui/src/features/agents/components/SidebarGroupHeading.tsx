import { cn } from "@/lib/utils"

export function SidebarGroupHeading({
  label,
  className,
}: {
  label: string
  className?: string
}) {
  return (
    <span className={cn("min-w-0 truncate text-quaternary", className)}>
      {label}
    </span>
  )
}
