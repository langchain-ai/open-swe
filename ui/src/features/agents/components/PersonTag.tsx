import { AtIcon } from "@phosphor-icons/react/dist/ssr/At"

import { cn } from "@/lib/utils"

/** A tagged person, styled so it never reads as plain text or a file chip. */
export function PersonTag({
  name,
  className,
}: {
  name: string
  className?: string
}) {
  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center gap-px rounded-md bg-brand-subtle px-space-1 align-baseline leading-tight font-medium text-brand-primary select-none",
        className
      )}
      title={`@${name}`}
    >
      <AtIcon aria-hidden className="size-3 shrink-0" weight="bold" />
      <span className="truncate">{name}</span>
    </span>
  )
}
