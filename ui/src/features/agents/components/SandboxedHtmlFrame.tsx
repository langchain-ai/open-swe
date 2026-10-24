import { forwardRef, useEffect, useImperativeHandle, useRef } from "react"
import { recordClientError } from "@/lib/clientErrors"
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
  const frameRef = useRef<HTMLIFrameElement>(null)
  useImperativeHandle(ref, () => frameRef.current!, [])
  useEffect(() => {
    let reports = 0
    const receive = (event: MessageEvent<unknown>) => {
      if (
        !frameRef.current?.contentWindow ||
        event.source !== frameRef.current.contentWindow
      )
        return
      const data = event.data
      if (
        !data ||
        typeof data !== "object" ||
        !("type" in data) ||
        data.type !== "openswe:client-error" ||
        !("version" in data) ||
        data.version !== 1 ||
        !("message" in data) ||
        typeof data.message !== "string" ||
        !data.message.trim() ||
        data.message.length > 2000 ||
        reports >= 100
      )
        return
      reports += 1
      recordClientError({
        title: "Embedded artifact error",
        error: new Error(data.message),
      })
    }
    window.addEventListener("message", receive)
    return () => window.removeEventListener("message", receive)
  }, [html, src])
  return (
    <iframe
      ref={frameRef}
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
