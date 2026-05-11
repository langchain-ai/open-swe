/**
 * Unicode figures used across the TUI. Picks portable variants where possible.
 */

const isDarwin = process.platform === "darwin";

export const BLACK_CIRCLE = isDarwin ? "⏺" : "●";
export const BULLET = "∙";
export const TEARDROP_ASTERISK = "✻";
export const ARROW_RIGHT = "❯";
export const ARROW_RIGHT_THIN = "›";
export const ARROW_UP = "↑";
export const ARROW_DOWN = "↓";
export const CHECK = "✔";
export const CROSS = "✖";
export const ELLIPSIS = "…";
export const BLOCKQUOTE_BAR = "▎";
export const HEAVY_HORIZONTAL = "━";
export const LIGHT_HORIZONTAL = "─";
export const VERTICAL = "│";
export const CORNER_TL = "┌";
export const CORNER_TR = "┐";
export const CORNER_BL = "└";
export const CORNER_BR = "┘";
// The "tool result" indent glyph used to attach a child response under the
// parent (assistant text or tool use) line. Matches the reference TUI.
export const RESPONSE_BAR = "⎿";
export const SPINNER_FRAMES = [
  "⠋",
  "⠙",
  "⠹",
  "⠸",
  "⠼",
  "⠴",
  "⠦",
  "⠧",
  "⠇",
  "⠏",
];
export const TEXT_SPINNER_FRAMES = ["◐", "◓", "◑", "◒"];
