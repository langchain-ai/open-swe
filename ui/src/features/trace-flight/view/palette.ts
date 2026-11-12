/**
 * The game's colors: a grayscale cut-paper world from Macaw's neutral ramp, with
 * only the bird and your trace in brand blue. Every role names a token, so the
 * world follows the app's theme; the dark ramp is reversed, so some roles step
 * to a different shade there to keep the same depth order.
 */
export type Theme = "light" | "dark"

export type PaletteRole =
  | "sky"
  | "minimap"
  | "paper"
  | "ground"
  | "groundEdge"
  | "lane"
  | "pr"
  | "prLip"
  | "prText"
  | "terminal"
  | "terminalBar"
  | "terminalText"
  | "ci"
  | "ciFail"
  | "ciBadge"
  | "ciFailMark"
  | "ciMark"
  | "pickup"
  | "pickupMark"
  | "bird"
  | "accentText"
  | "hudText"
  | "hudChip"
  | "autopilot"

export type Palette = Readonly<Record<PaletteRole, string>>

const LIGHT: Readonly<Record<PaletteRole, string>> = {
  sky: "--neutral-50",
  minimap: "--neutral-100",
  paper: "--neutral-200",
  ground: "--neutral-300",
  groundEdge: "--neutral-400",
  lane: "--neutral-400",
  pr: "--neutral-400",
  prLip: "--neutral-500",
  prText: "--neutral-200",
  terminal: "--neutral-500",
  terminalBar: "--neutral-600",
  terminalText: "--neutral-300",
  ci: "--neutral-400",
  ciFail: "--neutral-500",
  ciBadge: "--neutral-500",
  ciFailMark: "--neutral-600",
  ciMark: "--neutral-100",
  pickup: "--neutral-500",
  pickupMark: "--neutral-50",
  // The brand icon swaps fill and background between themes; the blue is the fill here.
  bird: "--icon-brand-fill",
  accentText: "--neutral-900",
  hudText: "--neutral-700",
  hudChip: "--neutral-25",
  autopilot: "--border-purple",
}

const DARK: Readonly<Record<PaletteRole, string>> = {
  ...LIGHT,
  terminal: "--neutral-300",
  terminalBar: "--neutral-400",
  terminalText: "--neutral-400",
  ciBadge: "--neutral-300",
  ciFailMark: "--neutral-400",
  ciMark: "--neutral-600",
  bird: "--icon-brand-background",
  accentText: "--neutral-25",
}

/** Reads the tokens the app's theme has set on the document. */
export function readPalette(theme: Theme): Palette {
  const style = getComputedStyle(document.documentElement)
  const roles = theme === "dark" ? DARK : LIGHT
  const entries = Object.entries(roles).map(([role, token]) => [
    role,
    style.getPropertyValue(token).trim(),
  ])
  return Object.fromEntries(entries) as Palette
}
