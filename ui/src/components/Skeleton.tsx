// oxlint-disable-next-line no-restricted-imports -- this module is the sanctioned wrapper
import * as MacawSkeleton from "@langchain/macaw-components/Skeleton"
import type { ComponentProps } from "react"

import { cn } from "@/lib/utils"

/** Macaw's placeholder block, with the shimmer sweep LangSmith's loading states use. */
export function Skeleton({
  className,
  ...props
}: ComponentProps<typeof MacawSkeleton.Skeleton>) {
  return (
    <MacawSkeleton.Skeleton
      className={cn("skeleton-shimmer", className)}
      {...props}
    />
  )
}

export function CircleSkeleton({
  className,
  ...props
}: ComponentProps<typeof MacawSkeleton.CircleSkeleton>) {
  return (
    <MacawSkeleton.CircleSkeleton
      className={cn("skeleton-shimmer", className)}
      {...props}
    />
  )
}

/** Stacked lines of text, the last one shorter so it reads as a paragraph. */
export function SkeletonLines({
  lines,
  label,
  className,
  lineClassName,
}: {
  lines: number
  /** Announces the placeholder as a loading status with this name. */
  label?: string
  className?: string
  lineClassName?: string
}) {
  return (
    <div
      role={label ? "status" : undefined}
      aria-label={label}
      className={cn("flex flex-col gap-space-2", className)}
    >
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton
          key={index}
          className={cn(
            "h-3",
            index === lines - 1 && lines > 1 ? "w-3/5" : "w-full",
            lineClassName
          )}
        />
      ))}
    </div>
  )
}
