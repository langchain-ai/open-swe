// Neutral filename foreground from the pierre Shiki themes (pierre-light /
// pierre-dark sidebar foreground). The tree tints filename text by git status,
// so feeding this keeps names neutral grey/white instead of accent-blue.
const TREE_FILE_FG = "light-dark(#525252, #a3a3a3)"

// Selected rows must read as high-contrast (white in dark, near-black in light)
// while the rest stay neutral. The built-in git-status content color outranks
// the selection color by specificity, so override it from the `unsafe` layer.
export const TREE_UNSAFE_CSS = `
  [data-item-selected="true"] [data-item-section="content"] {
    color: var(--trees-selected-fg);
  }

  /* On click a row is focus-ringed a frame before it's marked selected, which
   * flashes the accent outline. Pointer focus doesn't match :focus-visible, so
   * drop the ring there; keyboard navigation keeps it. */
  [data-item-focused="true"]:not(:focus-visible)::before {
    outline-color: transparent;
  }
`

export function treeThemeStyle(): React.CSSProperties {
  return {
    "--trees-theme-sidebar-bg": "var(--card)",
    "--trees-theme-sidebar-fg": "var(--foreground)",
    "--trees-theme-sidebar-border": "var(--border)",
    "--trees-theme-sidebar-header-fg": "var(--muted-foreground)",
    "--trees-theme-list-hover-bg":
      "color-mix(in oklab, var(--primary) 10%, transparent)",
    "--trees-theme-list-active-selection-bg":
      "color-mix(in oklab, var(--primary) 22%, transparent)",
    "--trees-theme-list-active-selection-fg": "var(--foreground)",
    "--trees-selected-focused-border-color-override": "transparent",
    "--trees-theme-input-bg": "var(--card)",
    "--trees-theme-input-fg": "var(--foreground)",
    "--trees-theme-input-border": "var(--border)",
    "--trees-theme-focus-ring": "var(--primary)",
    "--trees-theme-scrollbar-thumb": "var(--border)",
    "--trees-theme-git-added-fg": TREE_FILE_FG,
    "--trees-theme-git-modified-fg": TREE_FILE_FG,
    "--trees-theme-git-deleted-fg": TREE_FILE_FG,
    "--trees-theme-git-renamed-fg": TREE_FILE_FG,
    "--trees-theme-git-untracked-fg": TREE_FILE_FG,
    "--trees-theme-git-ignored-fg": "var(--muted-foreground)",
  } as React.CSSProperties
}
