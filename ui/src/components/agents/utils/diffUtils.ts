import { useMemo } from "react";
import { preloadHighlighter } from "@pierre/diffs";
import { useResolvedTheme } from "@/lib/theme";

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
`;

export const diffOptions = {
  theme: { light: "pierre-light", dark: "pierre-dark" } as const,
  themeType: "system" as const,
  diffStyle: "unified" as const,
  overflow: "scroll" as const,
  disableFileHeader: true,
  unsafeCSS: DIFF_UNSAFE_CSS,
  collapsedContextThreshold: 4,
};

export function useDiffOptions() {
  const resolvedTheme = useResolvedTheme();
  return useMemo(
    () => ({ ...diffOptions, themeType: resolvedTheme }),
    [resolvedTheme]
  );
}

let highlighterWarmup: Promise<void> | null = null;

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
  if (typeof window === "undefined") return Promise.resolve();
  if (highlighterWarmup == null) {
    highlighterWarmup = preloadHighlighter({
      themes: [diffOptions.theme.light, diffOptions.theme.dark],
      langs: ["text"],
    }).catch((error) => {
      highlighterWarmup = null;
      throw error;
    });
  }
  return highlighterWarmup;
}
