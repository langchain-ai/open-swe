import type { CSSProperties, FC } from "react"

import { cn } from "@/lib/utils"

interface SkeletonProps {
  className?: string
  as?: "div" | "span"
  style?: CSSProperties
}

const Skeleton: FC<SkeletonProps> = ({ className, style, as = "div" }) => {
  const Component = as
  return (
    <Component
      className={cn(
        "h-4 w-full animate-pulse rounded-ls-md bg-ls-surface-level-3",
        className
      )}
      style={style}
    />
  )
}

function SkeletonRows({
  rows,
  className,
  skeletonClassName,
}: {
  rows: number
  className?: string
  skeletonClassName?: string
}) {
  return (
    <div className={cn("flex flex-col gap-space-2", className)}>
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className={skeletonClassName} />
      ))}
    </div>
  )
}

function CircleSkeleton({ className, style }: SkeletonProps) {
  return (
    <div
      className={cn(
        "size-5 animate-pulse rounded-full bg-ls-surface-level-3",
        className
      )}
      style={style}
    />
  )
}

export { CircleSkeleton, Skeleton, SkeletonRows }
