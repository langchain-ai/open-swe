import type { ReactNode } from "react";

export type ToolUseRenderContext = {
  verbose?: boolean;
};

export type ToolResultRenderContext = {
  args?: Record<string, unknown>;
  verbose?: boolean;
};

/**
 * UI definition for a single agent tool. Each render hook may return `null`
 * to fall back to the default fallback rendering.
 */
export type ToolUI = {
  /** Tool names this UI handles (first is canonical). */
  names: readonly string[];
  /** User-facing label, e.g. `Bash`, `Read`, `Update`. */
  userFacingName: (args: Record<string, unknown> | undefined) => string;
  /** Inline parenthetical, e.g. `Bash(npm test)`. Empty string hides parens. */
  renderToolUseMessage?: (
    args: Record<string, unknown>,
    ctx: ToolUseRenderContext,
  ) => ReactNode | string | null;
  /** Result body rendered with the `⎿` indent. Return null for default. */
  renderToolResultMessage?: (
    output: string,
    ctx: ToolResultRenderContext,
  ) => ReactNode | null;
  /** Error body rendered with the `⎿` indent. Return null for default. */
  renderToolErrorMessage?: (
    output: string,
    ctx: ToolResultRenderContext,
  ) => ReactNode | null;
};
