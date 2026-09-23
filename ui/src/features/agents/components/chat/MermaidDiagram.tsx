import { useEffect, useId, useState } from "react"
import type { ResolvedTheme } from "@/lib/theme"
import { useResolvedTheme } from "@/lib/theme"
import { CodeBlock } from "./CodeBlock"

interface MermaidDiagramProps {
  source: string
}

type RenderState =
  | { status: "pending" }
  | { status: "rendered"; svg: string }
  | { status: "failed" }

const SVG_CACHE = new Map<string, string>()

// mermaid.initialize is global, so renders are serialized to keep each one's theme.
let renderQueue: Promise<unknown> = Promise.resolve()

function renderMermaid(
  id: string,
  source: string,
  theme: ResolvedTheme
): Promise<string> {
  const run = renderQueue.then(async () => {
    const { default: mermaid } = await import("mermaid")
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      suppressErrorRendering: true,
      theme: theme === "dark" ? "dark" : "default",
    })
    const { svg } = await mermaid.render(id, source)
    return svg
  })
  renderQueue = run.catch(() => undefined)
  return run
}

export function MermaidDiagram({ source }: MermaidDiagramProps) {
  const theme = useResolvedTheme()
  const id = `mermaid-${useId().replace(/[^\w-]/g, "")}`
  const cacheKey = `${theme}::${source}`
  const cached = SVG_CACHE.get(cacheKey)
  const [state, setState] = useState<RenderState>(
    cached ? { status: "rendered", svg: cached } : { status: "pending" }
  )

  useEffect(() => {
    const hit = SVG_CACHE.get(cacheKey)
    if (hit) {
      // oxlint-disable-next-line react/set-state-in-effect
      setState({ status: "rendered", svg: hit })
      return
    }
    let cancelled = false
    renderMermaid(id, source, theme)
      .then((svg) => {
        if (SVG_CACHE.size >= 100) SVG_CACHE.clear()
        SVG_CACHE.set(cacheKey, svg)
        if (!cancelled) setState({ status: "rendered", svg })
      })
      .catch((err: unknown) => {
        console.warn("[mermaid] Render failed", err)
        if (!cancelled) setState({ status: "failed" })
      })
    return () => {
      cancelled = true
    }
  }, [cacheKey, id, source, theme])

  if (state.status !== "rendered") {
    return <CodeBlock text={source} language="mermaid" />
  }
  return (
    <div
      role="img"
      aria-label="Mermaid diagram"
      className="my-[0.65rem] flex max-w-full justify-center overflow-x-auto rounded-lg border border-border/70 bg-muted/30 p-3 [&_svg]:h-auto [&_svg]:max-w-full"
      dangerouslySetInnerHTML={{ __html: state.svg }}
    />
  )
}
