// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it } from "vitest"

import { ConnectorLogo } from "../ConnectorLogo"

afterEach(() => {
  cleanup()
  document.documentElement.classList.remove("dark")
})

it("uses the dark LMT icon, then falls back to the light icon and initial on errors", () => {
  document.documentElement.classList.add("dark")
  const light = "https://icons.example.com/connector.svg"
  const dark = "https://icons.example.com/connector-dark.svg"
  render(
    <ConnectorLogo
      name="Custom MCP"
      icons={{ icon_url: light, icon_dark_url: dark }}
    />
  )
  const icon = screen.getByAltText("")
  expect(icon.getAttribute("src")).toBe(dark)
  expect(icon.getAttribute("referrerpolicy")).toBe("no-referrer")
  fireEvent.error(icon)
  expect(screen.getByAltText("").getAttribute("src")).toBe(light)
  fireEvent.error(screen.getByAltText(""))
  expect(screen.queryByText("C")).not.toBeNull()
  expect(screen.queryByAltText("")).toBeNull()
})

it("uses an initial instead of guessing a logo from the name or loading an unsafe URL", () => {
  const { rerender } = render(<ConnectorLogo name="Linear" />)
  expect(screen.queryByText("L")).not.toBeNull()
  rerender(
    <ConnectorLogo
      name="Linear"
      icons={{ icon_url: "http://icons.example.com/linear.svg" }}
    />
  )
  expect(screen.queryByText("L")).not.toBeNull()
  expect(screen.queryByAltText("")).toBeNull()
})
