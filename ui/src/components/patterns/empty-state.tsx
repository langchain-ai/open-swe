import type { ComponentProps, ReactNode } from "react"

import { cn } from "@/lib/utils"

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
  ...props
}: Omit<ComponentProps<"div">, "title" | "children"> & {
  icon?: ReactNode
  title?: ReactNode
  description: ReactNode
  action?: ReactNode
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-xl border border-dashed border-border px-6 py-12 text-center text-xs text-muted-foreground",
        className
      )}
      {...props}
    >
      {icon && (
        <div aria-hidden="true" className="rounded-full bg-accent p-3">
          {icon}
        </div>
      )}
      {title && (
        <h3
          className={cn("text-sm font-medium text-foreground", icon && "mt-4")}
        >
          {title}
        </h3>
      )}
      <p className={cn("max-w-sm", title && "mt-1")}>{description}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
