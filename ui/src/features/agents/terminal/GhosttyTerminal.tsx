import { useLayoutEffect, useRef } from "react"

import { GhosttyTerminalSurface } from "./ghostty/surface"
import type { GhosttyTerminalFont } from "./ghostty/surface"
import type { GhosttyTheme } from "./ghostty/core"

export interface GhosttyTerminalProps {
  readonly className?: string
  readonly theme: GhosttyTheme
  readonly font?: GhosttyTerminalFont
  readonly onData: (data: string) => void
  readonly onResize: (cols: number, rows: number) => void
  readonly onSelectionChange?: () => void
  readonly onCopy?: (text: string) => void
  readonly beforeKey?: (event: KeyboardEvent) => boolean
  readonly onLinkActivate?: (text: string, event: MouseEvent) => void
  readonly onReady?: (surface: GhosttyTerminalSurface) => void
  readonly onError?: (error: unknown) => void
}

export function GhosttyTerminal(props: GhosttyTerminalProps) {
  const mountRef = useRef<HTMLDivElement>(null)
  const latest = useRef(props)
  useLayoutEffect(() => {
    latest.current = props
  }, [props])

  useLayoutEffect(() => {
    const mount = mountRef.current
    if (!mount) return
    let disposed = false
    let surface: GhosttyTerminalSurface | null = null
    void GhosttyTerminalSurface.create(mount, {
      theme: latest.current.theme,
      ...(latest.current.font ? { font: latest.current.font } : {}),
      onData: (data) => latest.current.onData(data),
      onResize: (cols, rows) => latest.current.onResize(cols, rows),
      onSelectionChange: () => latest.current.onSelectionChange?.(),
      onCopy: (text) => latest.current.onCopy?.(text),
      beforeKey: (event) => latest.current.beforeKey?.(event) ?? true,
      onLinkActivate: (text, event) =>
        latest.current.onLinkActivate?.(text, event),
    }).then(
      (created) => {
        if (disposed) created.dispose()
        else {
          surface = created
          latest.current.onReady?.(created)
          created.focus()
        }
      },
      (error: unknown) => latest.current.onError?.(error)
    )
    return () => {
      disposed = true
      surface?.dispose()
    }
  }, [])

  return <div ref={mountRef} className={props.className} />
}
