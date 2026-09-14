import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react"

export type Theme = "light" | "dark" | "system"
export type ResolvedTheme = "light" | "dark"

export const THEME_STORAGE_KEY = "open-swe-theme"
const THEME_CHANGE_EVENT = "open-swe-theme-change"

function isTheme(value: string | null): value is Theme {
  return value === "light" || value === "dark" || value === "system"
}

function readStoredTheme(): Theme {
  if (typeof window === "undefined") return "system"
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY)
  return isTheme(stored) ? stored : "system"
}

function systemPrefersDark(): boolean {
  if (typeof window === "undefined") return false
  return window.matchMedia("(prefers-color-scheme: dark)").matches
}

export function resolveTheme(theme: Theme): ResolvedTheme {
  if (theme === "system") return systemPrefersDark() ? "dark" : "light"
  return theme
}

/** Browser chrome colour per resolved theme, mirroring `--background`. */
export const THEME_COLOR = { light: "#fcfcfc", dark: "#0a0a0a" } as const

function applyTheme(resolved: ResolvedTheme) {
  if (typeof document === "undefined") return
  const root = document.documentElement
  root.classList.toggle("dark", resolved === "dark")
  root.style.colorScheme = resolved
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", THEME_COLOR[resolved])
}

/**
 * Point the desktop window's native appearance at the app's own theme, so
 * macOS draws the traffic lights to match the UI rather than the OS.
 *
 * Sends the preference, not the resolved value: pinning themeSource to
 * light/dark while the user chose "system" would flip `prefers-color-scheme`
 * underneath `resolveTheme`, which reads it back.
 */
function syncDesktopAppearance(theme: Theme) {
  void window.openSweDesktop?.setAppearance(theme)
}

/** Theme state with system detection, persistence, and `.dark` class syncing. */
export function useTheme() {
  const [theme, setThemeState] = useState<Theme>("system")
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>("light")
  const themeRef = useRef<Theme>("system")

  useEffect(() => {
    const syncPreference = () => {
      const stored = readStoredTheme()
      const resolved = resolveTheme(stored)
      themeRef.current = stored
      setThemeState(stored)
      setResolvedTheme(resolved)
      applyTheme(resolved)
      syncDesktopAppearance(stored)
    }
    syncPreference()

    const media = window.matchMedia("(prefers-color-scheme: dark)")
    const onChange = () => {
      if (themeRef.current !== "system") return
      const next = systemPrefersDark() ? "dark" : "light"
      setResolvedTheme(next)
      applyTheme(next)
    }
    media.addEventListener("change", onChange)
    window.addEventListener(THEME_CHANGE_EVENT, syncPreference)
    return () => {
      media.removeEventListener("change", onChange)
      window.removeEventListener(THEME_CHANGE_EVENT, syncPreference)
    }
  }, [])

  const setTheme = useCallback((next: Theme) => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(THEME_STORAGE_KEY, next)
      window.dispatchEvent(new Event(THEME_CHANGE_EVENT))
    }
    themeRef.current = next
    const resolved = resolveTheme(next)
    setThemeState(next)
    setResolvedTheme(resolved)
    applyTheme(resolved)
    syncDesktopAppearance(next)
  }, [])

  const toggleTheme = useCallback(() => {
    setTheme(resolvedTheme === "dark" ? "light" : "dark")
  }, [resolvedTheme, setTheme])

  return { theme, resolvedTheme, setTheme, toggleTheme }
}

function readDomResolvedTheme(): ResolvedTheme {
  if (typeof document === "undefined") return "light"
  return document.documentElement.classList.contains("dark") ? "dark" : "light"
}

/** Reactive resolved theme that tracks the root `.dark` class set by `useTheme`. */
function subscribeToDomTheme(onChange: () => void): () => void {
  const observer = new MutationObserver(onChange)
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["class"],
  })
  return () => observer.disconnect()
}

export function useResolvedTheme(): ResolvedTheme {
  return useSyncExternalStore(
    subscribeToDomTheme,
    readDomResolvedTheme,
    (): ResolvedTheme => "light"
  )
}
