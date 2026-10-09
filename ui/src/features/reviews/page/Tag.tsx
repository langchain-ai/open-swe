import type { ComponentProps } from "react"

import { cn } from "@/lib/utils"

/** A small outlined label beside a name or path: "bot", "Outdated", "Added". */
export function Tag({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      className={cn(
        "shrink-0 rounded-[4px] border border-border px-1 text-[10px] leading-4 text-muted-foreground",
        className
      )}
      {...props}
    />
  )
}
