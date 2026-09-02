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
  className?: string
  style?: CSSProperties
  testId?: string
  onLoad?: ReactEventHandler<HTMLIFrameElement>
}

export const SandboxedHtmlFrame = forwardRef<
  HTMLIFrameElement,
  SandboxedHtmlFrameProps
>(function SandboxedHtmlFrame(
  { html, src, title, sandbox = "", allow, className, style, testId, onLoad },
  ref
) {
  return (
    <iframe
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
