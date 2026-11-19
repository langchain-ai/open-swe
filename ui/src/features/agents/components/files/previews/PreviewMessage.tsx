import type { ReactElement } from "react"

import { Skeleton } from "@/components/Skeleton"
import { cn } from "@/lib/utils"

const PREVIEW_LINE_WIDTHS = [
  "w-2/5",
  "w-4/5",
  "w-3/5",
  "w-11/12",
  "w-1/2",
  "w-3/4",
  "w-1/3",
  "w-5/6",
  "w-2/3",
  "w-1/4",
]

/** A centered status line in place of a preview: loading, empty, or an error. */
export function PreviewMessage(props: { children: string; error?: boolean }) {
  return (
    <div
      className={cn(
        "flex min-h-0 flex-1 items-center justify-center px-space-6 text-center text-xs text-secondary",
        props.error && "text-error-secondary"
      )}
    >
      {props.children}
    </div>
  )
}

/** Ragged lines where a file's contents will appear. */
export function PreviewLoading() {
  return (
    <div
      role="status"
      aria-label="Loading preview"
      className="flex min-h-0 flex-1 flex-col gap-space-3 overflow-hidden px-space-6 py-space-4"
    >
      {PREVIEW_LINE_WIDTHS.map((width, index) => (
        <Skeleton key={index} className={cn("h-3 shrink-0", width)} />
      ))}
    </div>
  )
}

/** The placeholder for a preview query still loading, or `null` once it has data. */
export function queryMessage(query: { data: unknown }): ReactElement | null {
  if (query.data === undefined) return <PreviewLoading />
  return null
}
