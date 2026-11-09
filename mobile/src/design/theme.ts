import { DynamicColorIOS, useColorScheme, type ColorValue } from "react-native"

import type { GitState, PullRequestState } from "../core/models"

/** A color that follows the system appearance. */
function dynamic(light: string, dark: string): ColorValue {
  return DynamicColorIOS({ light, dark })
}

/**
 * Robin's palette: the Macaw semantic tokens the web and desktop dashboard
 * uses (`@langchain/macaw-tokens`), each with its light and dark value, plus
 * the dashboard's own dark overrides. Comments name the token each color is.
 * Like the dashboard, the app follows the system appearance.
 */
export const Theme = {
  /** `--bg-surface-level-1`: the page. */
  background: dynamic("#FFFFFF", "#111521"),
  /** `--bg-surface-level-2`: cards, rails and sheets one step above the page. */
  surface: dynamic("#F5F8FB", "#1B2030"),
  /**
   * User bubbles, selected tabs and raised controls. The dashboard sets
   * `--bg-user-message` and `--bg-elevated` to `--neutral-200` in dark mode.
   */
  surfaceRaised: dynamic("#EDF2F7", "#282E42"),
  /** `--border-subtle`, lowered to `--neutral-200` in dark mode as the dashboard does. */
  stroke: dynamic("#E2E8F0", "#282E42"),
  separator: dynamic("#E2E8F0", "#282E42"),

  /** `--text-primary`. */
  textPrimary: dynamic("#0F172A", "#F5F8FB"),
  /** `--text-tertiary`: supporting text and secondary icons. */
  textSecondary: dynamic("#475569", "#CBD5E1"),
  /** `--text-placeholder`: metadata and quiet icons. */
  textTertiary: dynamic("#64748B", "#8790AB"),

  /** `--bg-brand`: emphasized actions, toggles and selection. */
  brand: dynamic("#006DDD", "#0078F1"),
  /** `--text-brand-on-fill`: text and icons on `brand`. */
  onBrand: "#FFFFFF" as ColorValue,
  /** `--text-brand-tertiary`: links. */
  link: dynamic("#1566B8", "#0078F1"),
  /** `--text-brand-primary`: the dashboard's tone for a merged pull request. */
  brandText: dynamic("#0D3D77", "#5FBEF8"),

  /** `--icon-warning`. */
  attention: dynamic("#CD6002", "#FEC84B"),
  /** `--purple-500`. */
  review: dynamic("#6244A0", "#8B71CC"),
  /** `--neutral-400`: read rows and other settled markers. */
  muted: dynamic("#94A3B8", "#555D78"),
  /** `--neutral-500`: neutral actions, such as archiving. */
  neutralAction: dynamic("#64748B", "#8790AB"),

  /** `--text-success-secondary` on `--bg-success`. */
  success: dynamic("#079455", "#75E0A7"),
  successBackground: dynamic("#ECFDF3", "#053321"),
  /** `--text-error-secondary` on `--bg-error`. */
  error: dynamic("#D92D20", "#FDA29B"),
  errorBackground: dynamic("#FEF3F2", "#55160C"),
  /** `--text-purple` on `--bg-purple`: the merged badge. */
  purple: dynamic("#3B2560", "#C5B4F0"),
  purpleBackground: dynamic("#E3D9FF", "#190D38"),

  cornerRadius: 22,
} as const

export const Palette = {
  ...Theme,
  unread: Theme.brand,
  working: Theme.brand,
  addition: Theme.success,
  deletion: Theme.error,
}

/** A pull request's tone in agent rows, as the dashboard draws it. */
export function gitStateColor(state: GitState): ColorValue {
  switch (state) {
    case "merged":
      return Theme.brandText
    case "open":
      return Theme.success
    case "closed":
      return Theme.error
    default:
      return Theme.textSecondary
  }
}

export function gitStateSymbol(
  state: GitState
): "arrow.triangle.merge" | "arrow.triangle.pull" | "plusminus" | null {
  switch (state) {
    case "merged":
      return "arrow.triangle.merge"
    case "open":
    case "draft":
    case "closed":
      return "arrow.triangle.pull"
    case "changes":
      return "plusminus"
    case "noChanges":
      return null
  }
}

/** The dashboard's `PR_STATE_STYLES`: badge text and background per state. */
export function pullRequestBadge(state: PullRequestState): {
  color: ColorValue
  background: ColorValue
} {
  switch (state) {
    case "draft":
      return { color: Theme.textSecondary, background: Theme.surface }
    case "open":
      return { color: Theme.success, background: Theme.successBackground }
    case "merged":
      return { color: Theme.purple, background: Theme.purpleBackground }
    case "closed":
      return { color: Theme.error, background: Theme.errorBackground }
  }
}

/**
 * `--text-primary` as a plain color for the current appearance. Native header
 * items resolve a dynamic color once, to its light value, so headers take this.
 */
export function useHeaderTint(): string {
  return useColorScheme() === "dark" ? "#F5F8FB" : "#0F172A"
}
