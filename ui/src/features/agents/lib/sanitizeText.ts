/**
 * Codepoint ranges stripped from agent-authored text, as `[start, end]` pairs.
 *
 * React escapes markup, so this text cannot inject elements. It does not
 * neutralize these: a right-to-left override renders as reversed text, which
 * can make a task description or an error read as something other than what
 * the agent produced, and terminal escapes survive a copy out of the page.
 *
 * Tab (0x09) and newline (0x0a) are deliberately kept — they carry meaning in
 * agent output and the cards render pre-wrapped.
 */
const UNSAFE_RANGES: ReadonlyArray<readonly [number, number]> = [
  [0x00, 0x08], // C0 controls before tab
  [0x0b, 0x1f], // C0 controls after newline, incl. ESC
  [0x7f, 0x9f], // DEL + C1 controls
  [0x200b, 0x200f], // zero-width + LRM/RLM
  [0x202a, 0x202e], // bidi embedding / override
  [0x2060, 0x2064], // word joiner + invisible operators
  [0x2066, 0x2069], // bidi isolates
  [0xfeff, 0xfeff], // zero-width no-break space (BOM)
]

function isUnsafe(codePoint: number): boolean {
  return UNSAFE_RANGES.some(
    ([start, end]) => codePoint >= start && codePoint <= end
  )
}

/**
 * Strip control, bidi-override, and zero-width characters from LLM-authored
 * text before rendering. Applied to every string that originates in the
 * agent's own output: subagent task descriptions, results, and errors.
 */
export function sanitizeAgentText(text: string): string {
  let out = ""
  for (const char of text) {
    const codePoint = char.codePointAt(0)
    if (codePoint !== undefined && isUnsafe(codePoint)) continue
    out += char
  }
  return out
}
