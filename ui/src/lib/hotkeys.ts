import { useEffect, useRef } from "react"

export interface HotkeyOptions {
  enabled?: boolean
  preventDefault?: boolean
  enableInFormFields?: boolean
  ignoreRepeat?: boolean
}

interface ParsedCombo {
  key: string
  mod: boolean
  meta: boolean
  ctrl: boolean
  alt: boolean
  shift: boolean
}

function isMac(): boolean {
  if (typeof navigator === "undefined") return false
  return /mac|iphone|ipad|ipod/i.test(navigator.platform || navigator.userAgent)
}

/** Parse a combo string like "mod+b" or "shift+escape" into its parts. */
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
    .map((p) => p.trim())) {
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

function eventMatchesCombo(event: KeyboardEvent, combo: ParsedCombo): boolean {
  if (event.key.toLowerCase() !== combo.key) return false
  const mac = isMac()
  const expectMeta = combo.meta || (combo.mod && mac)
  const expectCtrl = combo.ctrl || (combo.mod && !mac)
  return (
    event.metaKey === expectMeta &&
    event.ctrlKey === expectCtrl &&
    event.altKey === combo.alt &&
    event.shiftKey === combo.shift
  )
}

function isFormField(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return (
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT" ||
    target.isContentEditable
  )
}

/**
 * Register a global keyboard shortcut. Use "mod" for the platform meta key
 * (Cmd on macOS, Ctrl elsewhere). Accepts one combo or several aliases.
 */
export function useHotkey(
  combo: string | Array<string>,
  handler: (event: KeyboardEvent) => void,
  options: HotkeyOptions = {}
) {
  const {
    enabled = true,
    preventDefault = true,
    enableInFormFields = false,
    ignoreRepeat = false,
  } = options
  const handlerRef = useRef(handler)
  handlerRef.current = handler

  const comboKey = Array.isArray(combo) ? combo.join(",") : combo

  useEffect(() => {
    if (!enabled || typeof window === "undefined") return
    const combos = comboKey.split(",").map(parseCombo)
    const onKeyDown = (event: KeyboardEvent) => {
      if (ignoreRepeat && event.repeat) return
      if (!enableInFormFields && isFormField(event.target)) return
      if (!combos.some((c) => eventMatchesCombo(event, c))) return
      if (preventDefault) event.preventDefault()
      handlerRef.current(event)
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [enabled, preventDefault, enableInFormFields, ignoreRepeat, comboKey])
}
