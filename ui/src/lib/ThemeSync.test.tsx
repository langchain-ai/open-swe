// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { ThemeSync } from "./ThemeSync"
import { useTheme } from "./theme"

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
  })

  afterEach(() => {
    window.localStorage.clear()
    document.documentElement.classList.remove("dark")
    document.documentElement.style.colorScheme = ""
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
    })
  })
})
