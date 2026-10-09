import { useMemo, useSyncExternalStore } from "react"
import { preloadHighlighter, registerCustomTheme } from "@pierre/diffs"
import type {
  VirtualFileMetrics,
  WorkerInitializationRenderOptions,
  WorkerPoolOptions,
} from "@pierre/diffs/react"
import { createCssVariablesTheme } from "shiki"
import { useResolvedTheme } from "@/lib/theme"

export type DiffStyle = "unified" | "split"
export type DiffOverflow = "scroll" | "wrap"

// Colors resolve from Macaw's --syntax-* tokens, which already switch with .dark.
const macawSyntaxTheme = createCssVariablesTheme({
  name: "macaw",
  variablePrefix: "--diffs-",
  fontStyle: false,
  variableDefaults: {
    foreground: "var(--syntax-plain)",
    "token-keyword": "var(--syntax-keyword)",
    "token-string": "var(--syntax-string)",
    "token-string-expression": "var(--syntax-string)",
    "token-comment": "var(--syntax-comment)",
    "token-constant": "var(--syntax-constant)",
    "token-function": "var(--syntax-function)",
    "token-parameter": "var(--syntax-property)",
    "token-punctuation": "var(--syntax-operator)",
    "token-link": "var(--syntax-tag)",
  },
})

const DIFF_THEME = { light: "macaw-light", dark: "macaw-dark" } as const

for (const type of ["light", "dark"] as const) {
  const name = DIFF_THEME[type]
  registerCustomTheme(name, async () => ({
    ...macawSyntaxTheme,
    name,
    type,
    colors: {
      ...macawSyntaxTheme.colors,
      "gitDecoration.addedResourceForeground": "var(--icon-success)",
      "gitDecoration.deletedResourceForeground": "var(--icon-error)",
      "gitDecoration.modifiedResourceForeground": "var(--icon-brand)",
    },
  }))
}

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
  --diffs-surface: var(--panel-diff-bg, var(--bg-surface-level-1));
  --diffs-bg: var(--diffs-surface) !important;
  --diffs-light-bg: var(--diffs-surface) !important;
  --diffs-dark-bg: var(--diffs-surface) !important;
  --diffs-token-light-bg: transparent;
  --diffs-token-dark-bg: transparent;

  --diffs-bg-context-override: var(--diffs-surface);
  --diffs-bg-hover-override: var(--bg-surface-level-1-hover);
  --diffs-bg-separator-override: var(--bg-surface-level-1-hover);
  --diffs-bg-buffer-override: var(--diffs-surface);

  --diffs-bg-addition-override: color-mix(in srgb, var(--diffs-surface) 80%, var(--bg-success-strong));
  --diffs-bg-addition-number-override: color-mix(in srgb, var(--diffs-surface) 75%, var(--bg-success-strong));
  --diffs-bg-addition-hover-override: color-mix(in srgb, var(--diffs-surface) 70%, var(--bg-success-strong));
  --diffs-bg-addition-emphasis-override: color-mix(in srgb, var(--diffs-surface) 60%, var(--bg-success-strong));

  --diffs-bg-deletion-override: color-mix(in srgb, var(--diffs-surface) 80%, var(--bg-error-strong));
  --diffs-bg-deletion-number-override: color-mix(in srgb, var(--diffs-surface) 75%, var(--bg-error-strong));
  --diffs-bg-deletion-hover-override: color-mix(in srgb, var(--diffs-surface) 70%, var(--bg-error-strong));
  --diffs-bg-deletion-emphasis-override: color-mix(in srgb, var(--diffs-surface) 60%, var(--bg-error-strong));

  --diffs-fg-number-override: var(--text-secondary);
  --diffs-font-size: 12px;
  --diffs-line-height: 1.5;
  --diffs-font-family: "SF Mono", "Fira Code", "Cascadia Code", Menlo, Monaco, monospace;

  background-color: var(--diffs-surface) !important;
}

[data-file-info] {
  background-color: var(--bg-surface-level-1-hover) !important;
  border-block-color: var(--border-default) !important;
  color: var(--text-primary) !important;
}

[data-diffs-header] {
  position: sticky !important;
  top: 0;
  z-index: 4;
  background-color: var(--bg-surface-level-1-hover) !important;
  border-bottom: 1px solid var(--border-default) !important;
}

[data-separator] {
  background-color: var(--bg-surface-level-1-hover) !important;
  color: var(--text-secondary) !important;
}

/* A selected line propagates [data-selected-line] onto its annotation row and
   gutter, bleeding the selection background behind inline annotation content.
   Keep the code line highlighted, but hold the annotation row at the panel bg. */
[data-line-annotation][data-selected-line],
[data-gutter-buffer="annotation"][data-selected-line] {
  --diffs-line-bg: var(--diffs-surface, var(--bg-surface-level-1)) !important;
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
  theme: DIFF_THEME,
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
  theme: DIFF_THEME,
  lineDiffType: "word-alt",
  maxLineDiffLength: 800,
  tokenizeMaxLineLength: 1200,
  langs: ["text"],
} satisfies WorkerInitializationRenderOptions

/** FNV-1a: a fast, stable fingerprint for cache keys and versions, not for security. */
export function hashText(text: string): number {
  let hash = 0x811c9dc5
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193)
  }
  return hash >>> 0
}

export function hashFileContents(contents: string): string {
  return hashText(contents).toString(36)
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
