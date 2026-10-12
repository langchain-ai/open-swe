import { useIsHydrated } from "@/lib/hydration"

interface ParsedCombo {
  key: string
  mod: boolean
  meta: boolean
  ctrl: boolean
  alt: boolean
  shift: boolean
}

export type ShortcutPlatform = "mac" | "other"

export function shortcutPlatform(): ShortcutPlatform {
  if (typeof navigator === "undefined") return "other"
  return /mac|iphone|ipad|ipod/i.test(navigator.platform || navigator.userAgent)
    ? "mac"
    : "other"
}

function parseCombo(combo: string): ParsedCombo {
  const parsed: ParsedCombo = {
    key: "",
    mod: false,
    meta: false,
    ctrl: false,
    alt: false,
    shift: false,
  }
  for (const part of combo
    .toLowerCase()
    .split("+")
    .map((value) => value.trim())) {
    switch (part) {
      case "":
        break
      case "mod":
        parsed.mod = true
        break
      case "meta":
      case "cmd":
      case "command":
        parsed.meta = true
        break
      case "ctrl":
      case "control":
        parsed.ctrl = true
        break
      case "alt":
      case "option":
        parsed.alt = true
        break
      case "shift":
        parsed.shift = true
        break
      default:
        parsed.key = part
    }
  }
  return parsed
}

/** Browser navigation the app must never take over, even where a page could intercept it. */
const BROWSER_SHORTCUTS = [
  "mod+l",
  "alt+d",
  "mod+t",
  "mod+w",
  "mod+r",
  "mod+shift+r",
  "mod+f",
  "mod+g",
  "mod+p",
  "mod+[",
  "mod+]",
]

function resolvedComboId(combo: ParsedCombo, platform: ShortcutPlatform) {
  return [
    (combo.meta || (combo.mod && platform === "mac")) && "meta",
    (combo.ctrl || (combo.mod && platform !== "mac")) && "ctrl",
    combo.alt && "alt",
    combo.shift && "shift",
    combo.key,
  ]
    .filter(Boolean)
    .join("+")
}

export function eventMatchesShortcut(
  event: KeyboardEvent,
  shortcut: string,
  platform = shortcutPlatform()
): boolean {
  const combo = parseCombo(shortcut)
  const id = resolvedComboId(combo, platform)
  if (
    BROWSER_SHORTCUTS.some(
      (reserved) => resolvedComboId(parseCombo(reserved), platform) === id
    )
  )
    return false
  if (event.key.toLowerCase() !== combo.key) return false
  const expectMeta = combo.meta || (combo.mod && platform === "mac")
  const expectCtrl = combo.ctrl || (combo.mod && platform !== "mac")
  const bareQuestionMark =
    combo.key === "?" &&
    !combo.mod &&
    !combo.meta &&
    !combo.ctrl &&
    !combo.alt &&
    !combo.shift
  return (
    event.metaKey === expectMeta &&
    event.ctrlKey === expectCtrl &&
    event.altKey === combo.alt &&
    (event.shiftKey === combo.shift || bareQuestionMark)
  )
}

function shortcutKeyLabel(key: string): string {
  const labels: Record<string, string> = {
    escape: "Esc",
    enter: "Enter",
    space: "Space",
    tab: "Tab",
  }
  return labels[key] ?? (key.length === 1 ? key.toUpperCase() : key)
}

export function formatShortcut(
  shortcut: string,
  platform = shortcutPlatform()
): string {
  const combo = parseCombo(shortcut)
  if (combo.key === "?" && combo.shift) return "?"

  if (platform === "mac") {
    const modifiers = [
      combo.mod || combo.meta ? "⌘" : "",
      combo.ctrl ? "⌃" : "",
      combo.alt ? "⌥" : "",
      combo.shift ? "⇧" : "",
    ].join("")
    return `${modifiers}${shortcutKeyLabel(combo.key)}`
  }

  const modifiers = [
    combo.mod || combo.ctrl ? "Ctrl" : "",
    combo.meta ? "Meta" : "",
    combo.alt ? "Alt" : "",
    combo.shift ? "Shift" : "",
  ].filter(Boolean)
  return [...modifiers, shortcutKeyLabel(combo.key)].join(" ")
}

export function useShortcutLabel(shortcut: string): string {
  const hydrated = useIsHydrated()
  return hydrated ? formatShortcut(shortcut) : formatShortcut(shortcut, "other")
}

function closestElement(target: EventTarget | null): Element | null {
  if (typeof Element === "undefined" || !(target instanceof Element))
    return null
  return target
}

export function isTypingContext(target: EventTarget | null): boolean {
  return Boolean(
    closestElement(target)?.closest(
      'input, textarea, select, iframe, [role="textbox"], [role="searchbox"], [contenteditable]:not([contenteditable="false"])'
    )
  )
}

export function isHotkeySuppressed(target: EventTarget | null): boolean {
  return Boolean(closestElement(target)?.closest('[data-hotkeys="ignore"]'))
}

/**
 * Whether a shortcut should stay active while the user is typing. Modifier
 * combos (e.g. Cmd/Ctrl+N) qualify; bare keys like `c` do not.
 */
export function isTypingSafeShortcut(shortcut: string): boolean {
  const combo = parseCombo(shortcut)
  return combo.mod || combo.meta || combo.ctrl || combo.alt
}

export function shouldIgnoreHotkey(
  event: KeyboardEvent,
  enableInFormFields = false,
  ignoreRepeat = true
): boolean {
  return (
    event.defaultPrevented ||
    event.isComposing ||
    (ignoreRepeat && event.repeat) ||
    isHotkeySuppressed(event.target) ||
    (!enableInFormFields && isTypingContext(event.target))
  )
}
