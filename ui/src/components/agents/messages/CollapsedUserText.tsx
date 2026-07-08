import { useMemo } from "react"

import { collapsedUserTextSegments } from "./collapsedUserTextSegments"

export function CollapsedUserText({
  text,
  className,
}: {
  text: string
  className?: string
}) {
  const segments = useMemo(() => collapsedUserTextSegments(text), [text])

  return (
    <div className={className}>
      {segments.map((segment, index) =>
        segment.type === "text" ? (
          <span key={`${index}-text`}>{segment.text}</span>
        ) : (
          <span
            key={`${index}-pasted`}
            aria-label={`${segment.label} collapsed in message history`}
            title="Pasted content is collapsed in message history"
            className="inline-flex max-w-full items-center rounded-md border border-[var(--ui-border)] bg-[var(--ui-panel-2)] px-1.5 py-0.5 align-baseline font-mono text-[11px] leading-4 text-[color:var(--ui-text-muted)]"
          >
            {segment.label}
          </span>
        )
      )}
    </div>
  )
}
