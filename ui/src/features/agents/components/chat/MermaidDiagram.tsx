import { useEffect, useId, useState } from "react"

import { CodeBlock } from "./CodeBlock"
import { useResolvedTheme } from "@/lib/theme"

// d3 and dagre ride along, so the renderer loads on first diagram rather than
// with the app.
let mermaidModule: Promise<typeof import("mermaid")> | undefined

/**
 * A `mermaid` fence, drawn.
 *
 * Chat markdown replaces Streamdown's `pre` component, which also replaces its
 * diagram path, so diagrams are rendered here instead. Source that mermaid
 * cannot parse falls back to the code block rather than throwing through the
 * markdown tree.
 */
export function MermaidDiagram({ text }: { text: string }) {
  const theme = useResolvedTheme()
  const id = useId().replace(/[^a-zA-Z0-9-]/g, "")
  // Keyed by the source it was produced from, so a new diagram never shows the
  // previous one's SVG and the effect needs no synchronous reset.
  const [result, setResult] = useState<{
    source: string
    svg: string | null
  } | null>(null)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const mermaid = (await (mermaidModule ??= import("mermaid"))).default
        mermaid.initialize({
          startOnLoad: false,
          // The source is model-authored, so keep mermaid's own sanitizer on.
          securityLevel: "strict",
          theme: theme === "dark" ? "dark" : "default",
        })
        const rendered = await mermaid.render(`mermaid-${id}`, text)
        if (!cancelled) setResult({ source: text, svg: rendered.svg })
      } catch {
        if (!cancelled) setResult({ source: text, svg: null })
      }
    })()
    return () => {
      cancelled = true
    }
  }, [id, text, theme])

  const current = result?.source === text ? result : null
  if (current?.svg === null) return <CodeBlock text={text} language="mermaid" />
  const svg = current?.svg ?? null
  if (svg === null) {
    return (
      <div
        className="my-2 h-24 animate-pulse rounded-lg bg-accent"
        aria-label="Rendering diagram"
      />
    )
  }
  return (
    <div
      className="my-2 overflow-x-auto"
      data-mermaid-diagram=""
      // mermaid emits the SVG as markup and sanitizes it under securityLevel
      // "strict"; there is no node-tree form to mount instead.
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  )
}
