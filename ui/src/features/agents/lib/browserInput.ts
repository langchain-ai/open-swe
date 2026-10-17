/**
 * Turns a person's mouse and keyboard on the live view into the messages the
 * browser window takes: Chrome DevTools input events, in page CSS pixels.
 */

/** A message from the live view to the browser tab. */
export type ViewerMessage = Record<string, unknown> & { type: string }

/** Size of the page a frame shows, in CSS pixels. */
export interface FrameMetadata {
  deviceWidth: number
  deviceHeight: number
}

interface ModifierKeys {
  altKey: boolean
  ctrlKey: boolean
  metaKey: boolean
  shiftKey: boolean
}

interface KeyPress extends ModifierKeys {
  key: string
  code: string
  keyCode: number
}

/** CDP's modifier bit field. */
export function modifiers(event: ModifierKeys): number {
  return (
    (event.altKey ? 1 : 0) |
    (event.ctrlKey ? 2 : 0) |
    (event.metaKey ? 4 : 0) |
    (event.shiftKey ? 8 : 0)
  )
}

function keyBase(event: KeyPress) {
  return {
    type: "key",
    key: event.key,
    code: event.code,
    keyCode: event.keyCode,
    modifiers: modifiers(event),
  }
}

/**
 * The message for a key going down, or null for a paste shortcut, whose text
 * the paste event carries instead.
 */
export function keyDownMessage(event: KeyPress): ViewerMessage | null {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "v")
    return null
  const base = keyBase(event)
  if (event.key === "Enter") return { ...base, event: "keyDown", text: "\r" }
  // A key that names one character types it, unless Ctrl or Cmd makes it a
  // shortcut. Ctrl+Alt together is AltGr on Windows, which types characters
  // such as @ on many layouts. Spreading counts an emoji as one character.
  const isCharacter = [...event.key].length === 1
  const isShortcut = (event.ctrlKey && !event.altKey) || event.metaKey
  if (isCharacter && !isShortcut) {
    return { ...base, event: "keyDown", text: event.key }
  }
  return { ...base, event: "rawKeyDown" }
}

export function keyUpMessage(event: KeyPress): ViewerMessage {
  return { ...keyBase(event), event: "keyUp" }
}

/**
 * Converts a pointer position on the shown frame to page CSS pixels: the frame
 * may be shown at any size, so the position scales by the page's own size.
 */
export function pagePoint(
  clientX: number,
  clientY: number,
  frame: { left: number; top: number; width: number; height: number },
  meta: FrameMetadata
): { x: number; y: number } {
  return {
    x: ((clientX - frame.left) / frame.width) * meta.deviceWidth,
    y: ((clientY - frame.top) / frame.height) * meta.deviceHeight,
  }
}

/** What the address bar should navigate to for what the person typed. */
export function addressUrl(typed: string): string | null {
  const url = typed.trim()
  if (!url) return null
  return /^[a-z][a-z0-9+.-]*:/i.test(url) ? url : `https://${url}`
}
