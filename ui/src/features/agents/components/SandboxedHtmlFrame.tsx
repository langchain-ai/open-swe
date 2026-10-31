import { forwardRef } from "react"
import type { CSSProperties, ReactEventHandler } from "react"

import { cn } from "@/lib/utils"

type SandboxedHtmlFrameProps = (
  | { html: string; src?: never }
  | { src: string; html?: never }
) & {
  title: string
  sandbox?: string
  allow?: string
  /** A policy the browser requires of every document the frame loads (Chromium). */
  csp?: string
  className?: string
  style?: CSSProperties
  testId?: string
  onLoad?: ReactEventHandler<HTMLIFrameElement>
}

export const SandboxedHtmlFrame = forwardRef<
  HTMLIFrameElement,
  SandboxedHtmlFrameProps
>(function SandboxedHtmlFrame(
  {
    html,
    src,
    title,
    sandbox = "",
    allow,
    csp,
    className,
    style,
    testId,
    onLoad,
  },
  ref
) {
  // React's types lack `csp`, which React still renders as an attribute.
  const required: Record<string, string> = csp ? { csp } : {}
  return (
    <iframe
      {...required}
      ref={ref}
      data-testid={testId}
      onLoad={onLoad}
      title={title}
      src={src}
      srcDoc={html}
      sandbox={sandbox}
      allow={allow}
      referrerPolicy="no-referrer"
      className={cn("block w-full border-0", className)}
      style={style}
    />
  )
})
