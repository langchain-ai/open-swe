import { useMemo } from "react"
import { preloadHighlighter } from "@pierre/diffs"
import type {
  VirtualFileMetrics,
  WorkerInitializationRenderOptions,
  WorkerPoolOptions,
} from "@pierre/diffs/react"
import { useResolvedTheme } from "@/lib/theme"

export type DiffStyle = "unified" | "split"

export const DIFF_UNSAFE_CSS = `
[data-diffs-header],
[data-diff],
[data-file],
[data-error-wrapper],
[data-virtualizer-buffer] {
  --diffs-bg: var(--ui-panel) !important;
  --diffs-light-bg: var(--ui-panel) !important;
  --diffs-dark-bg: var(--ui-panel) !important;
  --diffs-token-light-bg: transparent;
  --diffs-token-dark-bg: transparent;

  --diffs-bg-context-override: var(--ui-panel);
  --diffs-bg-hover-override: var(--ui-panel-2);
  --diffs-bg-separator-override: var(--ui-accent-bubble);
  --diffs-bg-buffer-override: var(--ui-bg);

  --diffs-bg-addition-override: color-mix(in srgb, var(--ui-panel) 80%, #22c55e);
  --diffs-bg-addition-number-override: color-mix(in srgb, var(--ui-panel) 75%, #22c55e);
  --diffs-bg-addition-hover-override: color-mix(in srgb, var(--ui-panel) 70%, #22c55e);
  --diffs-bg-addition-emphasis-override: color-mix(in srgb, var(--ui-panel) 60%, #22c55e);

  --diffs-bg-deletion-override: color-mix(in srgb, var(--ui-panel) 80%, #ef4444);
  --diffs-bg-deletion-number-override: color-mix(in srgb, var(--ui-panel) 75%, #ef4444);
  --diffs-bg-deletion-hover-override: color-mix(in srgb, var(--ui-panel) 70%, #ef4444);
  --diffs-bg-deletion-emphasis-override: color-mix(in srgb, var(--ui-panel) 60%, #ef4444);

  --diffs-fg-number-override: var(--ui-text-dim);
  --diffs-font-size: 12px;
  --diffs-line-height: 1.5;
  --diffs-font-family: "SF Mono", "Fira Code", "Cascadia Code", Menlo, Monaco, monospace;

  background-color: var(--ui-panel) !important;
}

[data-file-info] {
  background-color: var(--ui-accent-bubble) !important;
  border-block-color: var(--ui-border) !important;
  color: var(--ui-text) !important;
}

[data-diffs-header] {
  position: sticky !important;
  top: 0;
  z-index: 4;
  background-color: var(--ui-accent-bubble) !important;
  border-bottom: 1px solid var(--ui-border) !important;
}

[data-separator] {
  background-color: var(--ui-accent-bubble) !important;
  color: var(--ui-text-dim) !important;
}

/* A selected line propagates [data-selected-line] onto its annotation row and
   gutter, bleeding the selection background behind inline annotation content.
   Keep the code line highlighted, but hold the annotation row at the panel bg. */
[data-line-annotation][data-selected-line],
[data-gutter-buffer="annotation"][data-selected-line] {
  --diffs-line-bg: var(--ui-panel) !important;
}

/* Pin every code row to one exact, uniform height (kept in sync with
   DIFF_VIRTUAL_METRICS.lineHeight below). In scroll mode code never wraps, so a
   hard height won't clip content — it just makes the virtualizer's per-line
   estimate match measured layout, so scroll-to lands precisely instead of
   over/under-shooting as off-estimate rows reconcile while scrolling. */
[data-line] {
  height: 18px !important;
  min-height: 18px !important;
  max-height: 18px !important;
  line-height: 18px !important;
}
`

export const diffOptions = {
  theme: { light: "pierre-light", dark: "pierre-dark" } as const,
  themeType: "system" as const,
  diffStyle: "unified" as const,
  overflow: "scroll" as const,
  disableFileHeader: true,
  unsafeCSS: DIFF_UNSAFE_CSS,
  collapsedContextThreshold: 4,
  lineDiffType: "word-alt" as const,
  maxLineDiffLength: 800,
  tokenizeMaxLineLength: 1200,
  tokenizeMaxLength: 120_000,
}

export function useDiffOptions(diffStyle: DiffStyle = "unified") {
  const resolvedTheme = useResolvedTheme()
  return useMemo(
    () => ({ ...diffOptions, themeType: resolvedTheme, diffStyle }),
    [resolvedTheme, diffStyle]
  )
}

// Shared virtualization + worker-pool config for <Virtualizer>/<MultiFileDiff>.
// Tuned for the agent git panel and the PR reviews page; keep them aligned so
// both viewers window rows and offload highlighting identically.
export const DIFF_VIRTUALIZER_CONFIG = {
  overscrollSize: 1200,
  intersectionObserverMargin: 4800,
}

export const DIFF_VIRTUAL_METRICS = {
  hunkLineCount: 80,
  // Must match the hard `[data-line]` height pinned in DIFF_UNSAFE_CSS so the
  // virtualizer's pre-measurement estimate equals the measured row height.
  lineHeight: 18,
  diffHeaderHeight: 0,
  spacing: 8,
} satisfies Partial<VirtualFileMetrics>

export const DIFF_WORKER_POOL_OPTIONS = {
  workerFactory: () =>
    new Worker(
      new URL("@pierre/diffs/worker/worker-portable.js", import.meta.url),
      { type: "module" }
    ),
  poolSize: 2,
  totalASTLRUCacheSize: 120,
} satisfies WorkerPoolOptions

export const DIFF_WORKER_HIGHLIGHTER_OPTIONS = {
  theme: { light: "pierre-light", dark: "pierre-dark" },
  lineDiffType: "word-alt",
  maxLineDiffLength: 800,
  tokenizeMaxLineLength: 1200,
  langs: ["text"],
} satisfies WorkerInitializationRenderOptions

function hashFileContents(contents: string): string {
  let hash = 0x811c9dc5
  for (let i = 0; i < contents.length; i++) {
    hash ^= contents.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(36)
}

// Stable per-file content key so the worker pool dedupes highlight work across
// re-renders instead of re-tokenizing identical content. Added/removed/binary/
// oversized blobs arrive as null (see pr_diff.py); coerce to "" so the key never
// dereferences null — these files don't render a diff, so the exact key is moot.
export function fileContentsCacheKey(
  path: string,
  side: "old" | "new",
  contents: string | null | undefined
): string {
  const text = contents ?? ""
  return `${path}:${side}:${text.length}:${hashFileContents(text)}`
}

let highlighterWarmup: Promise<void> | null = null

/**
 * Pierre's <MultiFileDiff> renders an empty <diffs-container> on its first mount
 * when the shared Shiki highlighter (specifically its themes) hasn't loaded yet:
 * the cold-start render bails before painting and relies on an async repaint that
 * can be dropped — most reliably under React StrictMode's mount/unmount/mount,
 * which leaves a stale empty <pre> behind so the remounted instance no-ops.
 *
 * Warming the themes up-front makes that first render synchronous and non-empty.
 * Idempotent and client-only (preloadHighlighter creates a Shiki instance).
 */
export function warmDiffHighlighter(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve()
  if (highlighterWarmup == null) {
    highlighterWarmup = preloadHighlighter({
      themes: [diffOptions.theme.light, diffOptions.theme.dark],
      langs: ["text"],
    }).catch((error) => {
      highlighterWarmup = null
      throw error
    })
  }
  return highlighterWarmup
}
