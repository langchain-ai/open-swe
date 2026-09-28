import type { BrowserSurfaceRect } from "@/features/agents/browser/browserSurfaceStore"

export interface HostedBrowserWebviewWrapperStyle {
  readonly left: number
  readonly top: number
  readonly width: number
  readonly height: number
  readonly zIndex: number
  readonly pointerEvents: "auto" | "none"
  readonly visibility?: "hidden" | "visible"
}

export const HIDDEN_BROWSER_WEBVIEW_OFFSET = -100_000

/**
 * An active tab sits exactly over its slot. A hidden one is parked far
 * off-screen; on macOS it stays paintable because Electron can permanently
 * blank a webview that was `visibility: hidden`.
 */
export function resolveHostedBrowserWebviewWrapperStyle(input: {
  readonly active: boolean
  readonly keepPaintableWhenInactive: boolean
  readonly zIndex: number
  readonly rect: BrowserSurfaceRect | null
  readonly hiddenSize: { readonly width: number; readonly height: number }
}): HostedBrowserWebviewWrapperStyle {
  if (input.active && input.rect) {
    return {
      left: input.rect.x,
      top: input.rect.y,
      width: input.rect.width,
      height: input.rect.height,
      zIndex: input.zIndex,
      pointerEvents: "auto",
    }
  }
  return {
    left: HIDDEN_BROWSER_WEBVIEW_OFFSET,
    top: HIDDEN_BROWSER_WEBVIEW_OFFSET,
    width: input.hiddenSize.width,
    height: input.hiddenSize.height,
    zIndex: -1,
    pointerEvents: "none",
    visibility: input.keepPaintableWhenInactive ? "visible" : "hidden",
  }
}
