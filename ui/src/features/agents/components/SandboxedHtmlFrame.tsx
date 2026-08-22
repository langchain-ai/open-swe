import { forwardRef } from "react"
import type { CSSProperties } from "react"

import { cn } from "@/lib/utils"

type SandboxedHtmlFrameProps = (
  { html: string; src?: never } | { src: string; html?: never }
) & {
  title: string
  sandbox?: string
  allow?: string
  className?: string
  style?: CSSProperties
  testId?: string
}

export const SandboxedHtmlFrame = forwardRef<
  HTMLIFrameElement,
  SandboxedHtmlFrameProps
>(function SandboxedHtmlFrameComponent(
  { html, src, title, sandbox = "", allow, className, style, testId },
  ref
) {
  return (
    <iframe
      ref={ref}
      data-testid={testId}
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
