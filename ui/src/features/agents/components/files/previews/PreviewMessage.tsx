import type { ReactElement } from "react"

import { cn } from "@/lib/utils"

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

/** The message for a preview query still loading, or `null` once it has data. */
export function queryMessage(query: { data: unknown }): ReactElement | null {
  if (query.data === undefined) return <PreviewMessage>Loading…</PreviewMessage>
  return null
}
