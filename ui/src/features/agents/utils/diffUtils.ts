import { useMemo, useSyncExternalStore } from "react"
import { preloadHighlighter } from "@pierre/diffs"
import type {
  VirtualFileMetrics,
  WorkerInitializationRenderOptions,
  WorkerPoolOptions,
} from "@pierre/diffs/react"
import { useResolvedTheme } from "@/lib/theme"

export type DiffStyle = "unified" | "split"
export type DiffOverflow = "scroll" | "wrap"

const DIFF_OVERFLOW_STORAGE_KEY = "open-swe.diff.overflow"
const diffOverflowListeners = new Set<() => void>()
let storageListenerAttached = false

export function readStoredDiffOverflow(): DiffOverflow {
  if (typeof window === "undefined") return "scroll"
  return window.localStorage.getItem(DIFF_OVERFLOW_STORAGE_KEY) === "wrap"
    ? "wrap"
    : "scroll"
}

function subscribeToDiffOverflow(listener: () => void): () => void {
  diffOverflowListeners.add(listener)
  if (typeof window !== "undefined" && !storageListenerAttached) {
    window.addEventListener("storage", handleDiffOverflowStorage)
    storageListenerAttached = true
  }
  return () => {
    diffOverflowListeners.delete(listener)
    if (
      typeof window !== "undefined" &&
      storageListenerAttached &&
      diffOverflowListeners.size === 0
    ) {
      window.removeEventListener("storage", handleDiffOverflowStorage)
      storageListenerAttached = false
    }
  }
}

function handleDiffOverflowStorage(event: StorageEvent): void {
  if (event.key !== DIFF_OVERFLOW_STORAGE_KEY) return
  diffOverflowListeners.forEach((listener) => listener())
}

export function writeStoredDiffOverflow(overflow: DiffOverflow): void {
  if (typeof window === "undefined") return
  if (readStoredDiffOverflow() === overflow) return
  window.localStorage.setItem(DIFF_OVERFLOW_STORAGE_KEY, overflow)
  diffOverflowListeners.forEach((listener) => listener())
}

export function useDiffOverflow(): [
  DiffOverflow,
  (next: DiffOverflow) => void,
] {
  const overflow = useSyncExternalStore(
    subscribeToDiffOverflow,
    readStoredDiffOverflow,
    (): DiffOverflow => "scroll"
  )
  return [overflow, writeStoredDiffOverflow]
}

export const DIFF_UNSAFE_CSS = `
[data-diffs-header],
[data-diff],
[data-file],
[data-error-wrapper],
[data-virtualizer-buffer] {
  --diffs-bg: var(--card) !important;
  --diffs-light-bg: var(--card) !important;
  --diffs-dark-bg: var(--card) !important;
  --diffs-token-light-bg: transparent;
  --diffs-token-dark-bg: transparent;

  --diffs-bg-context-override: var(--card);
  --diffs-bg-hover-override: var(--accent);
  --diffs-bg-separator-override: var(--accent);
  --diffs-bg-buffer-override: var(--card);

  --diffs-bg-addition-override: color-mix(in srgb, var(--card) 80%, #22c55e);
  --diffs-bg-addition-number-override: color-mix(in srgb, var(--card) 75%, #22c55e);
  --diffs-bg-addition-hover-override: color-mix(in srgb, var(--card) 70%, #22c55e);
  --diffs-bg-addition-emphasis-override: color-mix(in srgb, var(--card) 60%, #22c55e);

  --diffs-bg-deletion-override: color-mix(in srgb, var(--card) 80%, #ef4444);
  --diffs-bg-deletion-number-override: color-mix(in srgb, var(--card) 75%, #ef4444);
  --diffs-bg-deletion-hover-override: color-mix(in srgb, var(--card) 70%, #ef4444);
  --diffs-bg-deletion-emphasis-override: color-mix(in srgb, var(--card) 60%, #ef4444);

  --diffs-fg-number-override: var(--muted-foreground);
  --diffs-font-size: 12px;
  --diffs-line-height: 1.5;
  --diffs-font-family: "SF Mono", "Fira Code", "Cascadia Code", Menlo, Monaco, monospace;

  background-color: var(--card) !important;
}

[data-file-info] {
  background-color: var(--accent) !important;
  border-block-color: var(--border) !important;
  color: var(--foreground) !important;
}

[data-diffs-header] {
  position: sticky !important;
  top: 0;
  z-index: 4;
  background-color: var(--accent) !important;
  border-bottom: 1px solid var(--border) !important;
}

[data-separator] {
  background-color: var(--accent) !important;
  color: var(--muted-foreground) !important;
}

/* A selected line propagates [data-selected-line] onto its annotation row and
   gutter, bleeding the selection background behind inline annotation content.
   Keep the code line highlighted, but hold the annotation row at the panel bg. */
[data-line-annotation][data-selected-line],
[data-gutter-buffer="annotation"][data-selected-line] {
  --diffs-line-bg: var(--card) !important;
}
`

export const DIFF_FIXED_LINE_HEIGHT_CSS = `
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
  unsafeCSS: `${DIFF_UNSAFE_CSS}${DIFF_FIXED_LINE_HEIGHT_CSS}`,
  collapsedContextThreshold: 4,
  lineDiffType: "word-alt" as const,
  maxLineDiffLength: 800,
  tokenizeMaxLineLength: 1200,
  tokenizeMaxLength: 120_000,
}

export function buildDiffOptions(
  diffStyle: DiffStyle,
  overflow: DiffOverflow,
  themeType: "light" | "dark"
) {
  return {
    ...diffOptions,
    themeType,
    diffStyle,
    overflow,
    unsafeCSS:
      overflow === "scroll"
        ? `${DIFF_UNSAFE_CSS}${DIFF_FIXED_LINE_HEIGHT_CSS}`
        : DIFF_UNSAFE_CSS,
  }
}

export function useDiffOptions(diffStyle: DiffStyle = "unified") {
  const resolvedTheme = useResolvedTheme()
  const [overflow] = useDiffOverflow()
  return useMemo(
    () => buildDiffOptions(diffStyle, overflow, resolvedTheme),
    [resolvedTheme, diffStyle, overflow]
  )
}

export function useDiffWrap(): [boolean, (wrap: boolean) => void] {
  const [overflow, setOverflow] = useDiffOverflow()
  return [overflow === "wrap", (wrap) => setOverflow(wrap ? "wrap" : "scroll")]
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
  // Exact in scroll mode; in wrap mode this seeds the estimate until Pierre
  // measures the variable-height row and reconciles the virtualized layout.
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
