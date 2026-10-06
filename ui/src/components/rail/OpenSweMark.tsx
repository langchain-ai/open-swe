import type { CSSProperties } from "react"

import { cn } from "@langchain/gtm-platform-design-system/ui/cn"

const MARK_URL = `url(${import.meta.env.BASE_URL}logo-mark.png)`

// The mark ships as a raster; masking it paints it in the token ink instead of
// the asset's own blue, so the tile stays mono in both themes.
const MARK_STYLE: CSSProperties = {
  maskImage: MARK_URL,
  WebkitMaskImage: MARK_URL,
  maskSize: "contain",
  WebkitMaskSize: "contain",
  maskRepeat: "no-repeat",
  WebkitMaskRepeat: "no-repeat",
  maskPosition: "center",
  WebkitMaskPosition: "center",
}

/** The Open SWE pinwheel in the current ink. */
export function OpenSweMark({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      data-slot="open-swe-mark"
      style={MARK_STYLE}
      className={cn("inline-block size-4 shrink-0 bg-current", className)}
    />
  )
}

/** The rail's mark tile: mono `bg-mark` / `text-mark-ink`, one hairline. */
export function OpenSweMarkTile({ className }: { className?: string }) {
  return (
    <span
      data-slot="open-swe-mark-tile"
      className={cn(
        "inline-flex size-6 shrink-0 items-center justify-center rounded-badge border border-line-strong bg-mark text-mark-ink",
        className
      )}
    >
      <OpenSweMark className="size-3.5" />
    </span>
  )
}
