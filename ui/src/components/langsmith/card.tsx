import { forwardRef } from "react"
import type { HTMLAttributes } from "react"

import { cn } from "@/lib/utils"

export type CardIntent = "plain" | "neutral" | "info"

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  intent?: CardIntent
}

const intentClasses: Record<CardIntent, string> = {
  plain: "border-ls-muted bg-ls-elevated",
  neutral: "border-ls-muted bg-ls-surface-level-2",
  info: "border-ls-border-brand-subtle bg-ls-brand-surface",
}

const Card = forwardRef<HTMLDivElement, CardProps>(
  ({ intent = "neutral", className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "rounded-ls-lg border p-space-4 shadow-ls-sm",
        intentClasses[intent],
        className
      )}
      {...props}
    />
  )
)

Card.displayName = "Card"

export { Card }
