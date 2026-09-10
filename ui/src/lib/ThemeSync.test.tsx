// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { ThemeSync } from "./ThemeSync"
import { THEME_COLOR, useTheme } from "./theme"

function themeColor() {
  return document
    .querySelector('meta[name="theme-color"]')
    ?.getAttribute("content")
}

function ThemeControl() {
  const { setTheme } = useTheme()
  return <button onClick={() => setTheme("light")}>Use light theme</button>
}

describe("ThemeSync", () => {
  const values = new Map<string, string>()
  const storage = {
    clear: () => values.clear(),
    getItem: (key: string) => values.get(key) ?? null,
    key: (index: number) => [...values.keys()][index] ?? null,
    get length() {
      return values.size
    },
    removeItem: (key: string) => values.delete(key),
    setItem: (key: string, value: string) => values.set(key, value),
  } as Storage

  beforeEach(() => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: storage,
    })
    const meta = document.createElement("meta")
    meta.setAttribute("name", "theme-color")
    meta.setAttribute("content", THEME_COLOR.light)
    document.head.append(meta)
  })

  afterEach(() => {
    window.localStorage.clear()
    document.documentElement.classList.remove("dark")
    document.documentElement.style.colorScheme = ""
    document.querySelector('meta[name="theme-color"]')?.remove()
    vi.restoreAllMocks()
  })

  it("applies the system theme when no preference control is mounted", async () => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: () => ({
        matches: true,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }),
    })

    render(<ThemeSync />)

    await waitFor(() => {
      expect(document.documentElement.classList.contains("dark")).toBe(true)
      expect(document.documentElement.style.colorScheme).toBe("dark")
      expect(themeColor()).toBe(THEME_COLOR.dark)
    })
  })

  it("does not overwrite an explicit preference when the system theme changes", async () => {
    let prefersDark = false
    const listeners = new Set<() => void>()
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: () => ({
        get matches() {
          return prefersDark
        },
        addEventListener: (_event: string, listener: () => void) =>
          listeners.add(listener),
        removeEventListener: (_event: string, listener: () => void) =>
          listeners.delete(listener),
      }),
    })

    render(
      <>
        <ThemeSync />
        <ThemeControl />
      </>
    )
    fireEvent.click(screen.getByRole("button", { name: "Use light theme" }))
    prefersDark = true
    listeners.forEach((listener) => listener())

    await waitFor(() => {
      expect(document.documentElement.classList.contains("dark")).toBe(false)
      expect(document.documentElement.style.colorScheme).toBe("light")
      expect(themeColor()).toBe(THEME_COLOR.light)
    })
  })
})
