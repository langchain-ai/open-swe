import { useCallback, useEffect, useRef, useState } from "react"

export type Theme = "light" | "dark" | "system"
export type ResolvedTheme = "light" | "dark"

export const THEME_STORAGE_KEY = "open-swe-theme"

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

function applyTheme(resolved: ResolvedTheme) {
  if (typeof document === "undefined") return
  const root = document.documentElement
  root.classList.toggle("dark", resolved === "dark")
  root.style.colorScheme = resolved
}

/** Theme state with system detection, persistence, and `.dark` class syncing. */
export function useTheme() {
  const [theme, setThemeState] = useState<Theme>("system")
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>("light")
  const themeRef = useRef<Theme>("system")

  useEffect(() => {
    const stored = readStoredTheme()
    themeRef.current = stored
    setThemeState(stored)
    setResolvedTheme(resolveTheme(stored))
    applyTheme(resolveTheme(stored))

    const media = window.matchMedia("(prefers-color-scheme: dark)")
    const onChange = () => {
      if (themeRef.current !== "system") return
      const next = systemPrefersDark() ? "dark" : "light"
      setResolvedTheme(next)
      applyTheme(next)
    }
    media.addEventListener("change", onChange)
    return () => media.removeEventListener("change", onChange)
  }, [])

  const setTheme = useCallback((next: Theme) => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(THEME_STORAGE_KEY, next)
    }
    themeRef.current = next
    const resolved = resolveTheme(next)
    setThemeState(next)
    setResolvedTheme(resolved)
    applyTheme(resolved)
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
export function useResolvedTheme(): ResolvedTheme {
  const [resolved, setResolved] = useState<ResolvedTheme>(readDomResolvedTheme)

  useEffect(() => {
    setResolved(readDomResolvedTheme())
    const observer = new MutationObserver(() =>
      setResolved(readDomResolvedTheme())
    )
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    })
    return () => observer.disconnect()
  }, [])

  return resolved
}
