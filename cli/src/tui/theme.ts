/**
 * Theme tokens for the coda TUI.
 *
 * The palette is intentionally restrained: a warm cream foreground sits on top
 * of the terminal's own dark background, with a single amber/gold brand accent
 * for the logo, bullets and prompt glyph, and a cool blue used sparingly for
 * file paths, highlights and menu selections. This mirrors the look of modern
 * terminal coding agents (Codex, Cursor CLI) where most of the surface is
 * monochrome and color is reserved for signal.
 *
 * All values are RGB strings consumable by Ink's `color` / `backgroundColor`.
 */

export type ThemeName = "dark" | "light";

export type Theme = {
  brand: string;
  brandDim: string;
  text: string;
  inverseText: string;
  subtle: string;
  inactive: string;
  suggestion: string;
  permission: string;
  planMode: string;
  bashBorder: string;
  promptBorder: string;
  success: string;
  error: string;
  warning: string;
  diffAdded: string;
  diffRemoved: string;
  diffAddedDimmed: string;
  diffRemovedDimmed: string;
  diffAddedWord: string;
  diffRemovedWord: string;
  syntaxKeyword: string;
  syntaxString: string;
  syntaxComment: string;
  syntaxNumber: string;
  syntaxFunction: string;
  userMessageBg: string;
  selectionBg: string;
  background: string;
};

const dark: Theme = {
  brand: "rgb(214,184,116)",
  brandDim: "rgb(132,108,64)",
  text: "rgb(222,213,191)",
  inverseText: "rgb(20,18,15)",
  subtle: "rgb(94,88,78)",
  inactive: "rgb(140,132,118)",
  suggestion: "rgb(140,172,224)",
  permission: "rgb(140,172,224)",
  planMode: "rgb(178,202,134)",
  bashBorder: "rgb(184,134,168)",
  promptBorder: "rgb(94,88,78)",
  success: "rgb(178,202,134)",
  error: "rgb(225,134,124)",
  warning: "rgb(214,184,116)",
  diffAdded: "rgb(38,52,28)",
  diffRemoved: "rgb(64,32,34)",
  diffAddedDimmed: "rgb(26,34,20)",
  diffRemovedDimmed: "rgb(44,24,26)",
  diffAddedWord: "rgb(64,92,42)",
  diffRemovedWord: "rgb(104,44,44)",
  syntaxKeyword: "rgb(190,168,236)",
  syntaxString: "rgb(178,202,134)",
  syntaxComment: "rgb(118,112,98)",
  syntaxNumber: "rgb(140,172,224)",
  syntaxFunction: "rgb(224,188,128)",
  userMessageBg: "rgb(28,26,22)",
  selectionBg: "rgb(48,58,82)",
  background: "rgb(18,17,15)",
};

const light: Theme = {
  brand: "rgb(150,108,40)",
  brandDim: "rgb(196,170,118)",
  text: "rgb(40,34,24)",
  inverseText: "rgb(250,247,240)",
  subtle: "rgb(186,178,160)",
  inactive: "rgb(132,124,108)",
  suggestion: "rgb(58,92,168)",
  permission: "rgb(58,92,168)",
  planMode: "rgb(96,128,52)",
  bashBorder: "rgb(146,82,124)",
  promptBorder: "rgb(186,178,160)",
  success: "rgb(96,128,52)",
  error: "rgb(176,72,58)",
  warning: "rgb(150,108,40)",
  diffAdded: "rgb(224,236,196)",
  diffRemoved: "rgb(244,222,218)",
  diffAddedDimmed: "rgb(238,244,220)",
  diffRemovedDimmed: "rgb(248,236,234)",
  diffAddedWord: "rgb(194,222,156)",
  diffRemovedWord: "rgb(232,188,182)",
  syntaxKeyword: "rgb(106,74,168)",
  syntaxString: "rgb(78,124,54)",
  syntaxComment: "rgb(132,124,108)",
  syntaxNumber: "rgb(58,92,168)",
  syntaxFunction: "rgb(150,108,40)",
  userMessageBg: "rgb(244,238,224)",
  selectionBg: "rgb(214,226,248)",
  background: "rgb(250,247,240)",
};

const themes: Record<ThemeName, Theme> = { dark, light };

let activeTheme: ThemeName = "dark";

export function setTheme(name: ThemeName): void {
  activeTheme = name;
}

export function getTheme(): Theme {
  return themes[activeTheme];
}

export function themeColor(token: keyof Theme): string {
  return themes[activeTheme][token];
}
