import { cn } from "@/lib/utils"

export function SidebarGroupHeading({
  label,
  className,
}: {
  label: string
  className?: string
}) {
  return (
    <span
      className={cn(
        "mb-0.5 min-w-0 truncate text-sm text-quaternary [font-variant-caps:all-small-caps]",
        className
      )}
    >
      {label}
    </span>
  )
}
