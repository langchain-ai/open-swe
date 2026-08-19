import { useMemo } from "react"

import { SandboxedHtmlFrame } from "@/features/agents/components/SandboxedHtmlFrame"
import { useResolvedTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"

const ARTIFACT_CSP = [
  "default-src 'none'",
  "style-src 'unsafe-inline' https://fonts.googleapis.com",
  "font-src https://fonts.gstatic.com data:",
  "img-src data:",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-src 'none'",
  "connect-src 'none'",
].join("; ")

export function withViewerPolicy(
  html: string,
  theme: "light" | "dark"
): string {
  const policy = `<meta http-equiv="Content-Security-Policy" content="${ARTIFACT_CSP}">`
  const themed = html.replace(
    /<html(?=\s|>)/i,
    `<html data-theme="${theme}" data-viewer-theme="${theme}"`
  )
  if (/<head(?=\s|>)/i.test(themed)) {
    return themed.replace(/<head([^>]*)>/i, `<head$1>${policy}`)
  }
  return `<!doctype html><html data-theme="${theme}" data-viewer-theme="${theme}"><head>${policy}</head><body>${themed}</body></html>`
}

export function PlanArtifactFrame({
  html,
  title = "Plan artifact",
  className,
}: {
  html: string
  title?: string
  className?: string
}) {
  const theme = useResolvedTheme()
  const srcDoc = useMemo(() => withViewerPolicy(html, theme), [html, theme])

  return (
    <SandboxedHtmlFrame
      testId="plan-artifact-frame"
      title={title}
      html={srcDoc}
      className={cn("bg-background", className)}
    />
  )
}
