/**
 * Translates DOM keyboard events into Chrome DevTools Protocol
 * `Input.dispatchKeyEvent` payloads for the sandbox browser.
 */

/** CDP modifier bit flags. */
export const CDP_MODIFIER_ALT = 1
export const CDP_MODIFIER_CTRL = 2
export const CDP_MODIFIER_META = 4
export const CDP_MODIFIER_SHIFT = 8

const NAMED_KEY_CODES: Readonly<Record<string, number>> = {
  Backspace: 8,
  Tab: 9,
  Enter: 13,
  NumpadEnter: 13,
  ShiftLeft: 16,
  ShiftRight: 16,
  ControlLeft: 17,
  ControlRight: 17,
  AltLeft: 18,
  AltRight: 18,
  Pause: 19,
  CapsLock: 20,
  Escape: 27,
  Space: 32,
  PageUp: 33,
  PageDown: 34,
  End: 35,
  Home: 36,
  ArrowLeft: 37,
  ArrowUp: 38,
  ArrowRight: 39,
  ArrowDown: 40,
  PrintScreen: 44,
  Insert: 45,
  Delete: 46,
  MetaLeft: 91,
  MetaRight: 93,
  ContextMenu: 93,
  NumpadMultiply: 106,
  NumpadAdd: 107,
  NumpadSubtract: 109,
  NumpadDecimal: 110,
  NumpadDivide: 111,
  NumLock: 144,
  ScrollLock: 145,
  Semicolon: 186,
  Equal: 187,
  Comma: 188,
  Minus: 189,
  Period: 190,
  Slash: 191,
  Backquote: 192,
  BracketLeft: 219,
  Backslash: 220,
  BracketRight: 221,
  Quote: 222,
  IntlBackslash: 226,
}

export function windowsVirtualKeyCode(code: string, key: string): number {
  const named = NAMED_KEY_CODES[code]
  if (named !== undefined) return named
  const letter = /^Key([A-Z])$/.exec(code)
  if (letter) return letter[1]!.charCodeAt(0)
  const digit = /^Digit(\d)$/.exec(code)
  if (digit) return 48 + Number(digit[1])
  const numpad = /^Numpad(\d)$/.exec(code)
  if (numpad) return 96 + Number(numpad[1])
  const functionKey = /^F(\d{1,2})$/.exec(code)
  if (functionKey) {
    const number = Number(functionKey[1])
    if (number >= 1 && number <= 24) return 111 + number
  }
  // Unknown physical key: fall back to the character itself when printable.
  return key.length === 1 ? key.toUpperCase().charCodeAt(0) : 0
}

export function keyLocation(code: string): number {
  if (code.endsWith("Left")) return 1
  if (code.endsWith("Right")) return 2
  if (code.startsWith("Numpad")) return 3
  return 0
}

export interface CdpKeyModifierState {
  readonly altKey: boolean
  readonly ctrlKey: boolean
  readonly metaKey: boolean
  readonly shiftKey: boolean
}

/**
 * Modifier mask for the remote browser. The sandbox Chromium runs on Linux,
 * where editing shortcuts use Control; a macOS user pressing Command expects
 * the same result, so Command is sent as Control. `swapMetaForControl` is
 * therefore true on Apple platforms.
 */
export function cdpModifiers(
  event: CdpKeyModifierState,
  swapMetaForControl: boolean
): number {
  let modifiers = 0
  if (event.altKey) modifiers |= CDP_MODIFIER_ALT
  if (event.shiftKey) modifiers |= CDP_MODIFIER_SHIFT
  if (event.ctrlKey) modifiers |= CDP_MODIFIER_CTRL
  if (event.metaKey)
    modifiers |= swapMetaForControl ? CDP_MODIFIER_CTRL : CDP_MODIFIER_META
  return modifiers
}

export interface CdpKeyEventInput {
  readonly type: "keyDown" | "rawKeyDown" | "keyUp"
  readonly key: string
  readonly code: string
  readonly windowsVirtualKeyCode: number
  readonly modifiers: number
  readonly location: number
  readonly isKeypad: boolean
  readonly autoRepeat: boolean
  readonly text?: string
  readonly unmodifiedText?: string
}

export interface DomKeyLike extends CdpKeyModifierState {
  readonly key: string
  readonly code: string
  readonly repeat: boolean
}

/**
 * Builds the down and up packets for one DOM key event. A printable key
 * without Control/Alt/Meta carries `text`, so Chromium synthesises the input;
 * a shortcut goes as `rawKeyDown` with no text, as a physical key would.
 */
export function cdpKeyEvents(
  event: DomKeyLike,
  swapMetaForControl: boolean
): { readonly down: CdpKeyEventInput; readonly up: CdpKeyEventInput } {
  const modifiers = cdpModifiers(event, swapMetaForControl)
  const printable =
    event.key.length === 1 && (modifiers & ~CDP_MODIFIER_SHIFT) === 0
  const text = printable ? event.key : event.key === "Enter" ? "\r" : ""
  const location = keyLocation(event.code)
  const shared = {
    key: event.key,
    code: event.code,
    windowsVirtualKeyCode: windowsVirtualKeyCode(event.code, event.key),
    modifiers,
    location,
    isKeypad: location === 3,
    autoRepeat: event.repeat,
  }
  return {
    down: {
      type: text ? "keyDown" : "rawKeyDown",
      ...shared,
      ...(text ? { text, unmodifiedText: text } : {}),
    },
    up: { type: "keyUp", ...shared },
  }
}
