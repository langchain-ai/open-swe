import { forwardRef } from "react"
import type { CSSProperties } from "react"

import { cn } from "@/lib/utils"

export const SandboxedHtmlFrame = forwardRef<
  HTMLIFrameElement,
  {
    html: string
    title: string
    sandbox?: string
    allow?: string
    className?: string
    style?: CSSProperties
    testId?: string
  }
>(function SandboxedHtmlFrame(
  { html, title, sandbox = "", allow, className, style, testId },
  ref
) {
  return (
    <iframe
      ref={ref}
      data-testid={testId}
      title={title}
      srcDoc={html}
      sandbox={sandbox}
      allow={allow}
      referrerPolicy="no-referrer"
      className={cn("block w-full border-0", className)}
      style={style}
    />
  )
})
