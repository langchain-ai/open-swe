import type { ComponentProps, ReactNode } from "react"

import { cn } from "@/lib/utils"

export function PageHeader({
  title,
  description,
  actions,
  size = "default",
  className,
  ...props
}: Omit<ComponentProps<"header">, "title" | "children"> & {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  size?: "default" | "compact"
}) {
  return (
    <header
      className={cn(
        "flex flex-wrap items-start justify-between gap-3",
        className
      )}
      {...props}
    >
      <div className="min-w-0">
        <h1
          className={cn(
            "font-heading font-medium text-foreground",
            size === "compact" ? "text-base" : "text-xl tracking-tight"
          )}
        >
          {title}
        </h1>
        {description && (
          <p
            className={cn(
              "text-xs text-muted-foreground",
              size === "compact" ? "mt-1" : "mt-1.5 max-w-2xl"
            )}
          >
            {description}
          </p>
        )}
      </div>
      {actions}
    </header>
  )
}
