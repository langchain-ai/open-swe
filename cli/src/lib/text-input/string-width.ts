import npmStringWidth from "string-width";

/**
 * Display width of a string as it would appear in a terminal.
 * Uses the npm `string-width` package (which we already pull in transitively
 * via `wrap-ansi` / `ink`). Treats ambiguous-width characters as narrow,
 * matching the recommended Western default.
 */
export const stringWidth: (str: string) => number = (str) =>
  npmStringWidth(str, { ambiguousIsNarrow: true });
