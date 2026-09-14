import { useEffect, useState } from "react"

import {
  clearPerfSpans,
  exportPerfSpans,
  formatSpan,
  getPerfSpans,
  setPerfHudEnabled,
  subscribePerfSpans,
} from "./trace"
import type { PerfSpan } from "./trace"

const VISIBLE_SPANS = 8

function attributeSummary(span: PerfSpan): string {
  return Object.entries(span.attributes)
    .filter(([, value]) => value !== null && value !== false)
    .map(([key, value]) => (value === true ? key : `${key}=${String(value)}`))
    .join("  ")
}

/**
 * Development overlay for the perf spans, toggled with `?perf=1` / `?perf=0`
 * or `window.__openSwePerf.hud(true)`. Newest span first.
 */
export default function PerfHud() {
  const [spans, setSpans] = useState<ReadonlyArray<PerfSpan>>(() =>
    getPerfSpans()
  )
  const [expanded, setExpanded] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => subscribePerfSpans(() => setSpans([...getPerfSpans()])), [])

  const visible = spans.slice(-VISIBLE_SPANS).reverse()

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(exportPerfSpans())
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch {}
  }

  return (
    <aside
      aria-label="Performance"
      className="fixed bottom-3 left-3 z-[1000] w-[26rem] max-w-[calc(100vw-1.5rem)] rounded-md border border-border bg-background/95 font-mono text-[11px] leading-snug text-foreground shadow-lg backdrop-blur"
    >
      <header className="flex items-center gap-2 border-b border-border px-2 py-1">
        <span className="font-semibold">perf</span>
        <span className="text-muted-foreground">{spans.length} spans</span>
        <span className="flex-1" />
        <button type="button" className="hover:underline" onClick={copy}>
          {copied ? "copied" : "copy json"}
        </button>
        <button
          type="button"
          className="hover:underline"
          onClick={clearPerfSpans}
        >
          clear
        </button>
        <button
          type="button"
          className="hover:underline"
          onClick={() => setPerfHudEnabled(false)}
          aria-label="Hide performance overlay"
        >
          ×
        </button>
      </header>
      <ul className="max-h-[40vh] overflow-y-auto">
        {visible.length === 0 && (
          <li className="px-2 py-1 text-muted-foreground">
            Open a thread or send a message to record a span.
          </li>
        )}
        {visible.map((span) => (
          <li key={span.id} className="border-b border-border/60 last:border-0">
            <button
              type="button"
              className="w-full px-2 py-1 text-left hover:bg-accent/40"
              onClick={() =>
                setExpanded((current) => (current === span.id ? null : span.id))
              }
            >
              <span
                className={
                  span.status === "abandoned"
                    ? "text-muted-foreground line-through"
                    : span.status === "open"
                      ? "text-muted-foreground"
                      : undefined
                }
              >
                {formatSpan(span)}
              </span>
            </button>
            {expanded === span.id && (
              <p className="px-2 pb-1 break-all whitespace-pre-wrap text-muted-foreground">
                {attributeSummary(span) || "no attributes"}
              </p>
            )}
          </li>
        ))}
      </ul>
    </aside>
  )
}
