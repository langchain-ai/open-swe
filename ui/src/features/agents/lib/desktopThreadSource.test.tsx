/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it } from "vitest"

import { useDesktopThreadSource } from "./desktopThreadSource"

beforeEach(() => window.localStorage.clear())
afterEach(() => cleanup())

function SourceControl({ label }: { label: string }) {
  const [source, setSource] = useDesktopThreadSource()
  return (
    <button type="button" onClick={() => setSource("local")}>
      {label}: {source}
    </button>
  )
}

describe("useDesktopThreadSource", () => {
  it("defaults to cloud and synchronizes mounted consumers", () => {
    render(
      <>
        <SourceControl label="First" />
        <SourceControl label="Second" />
      </>
    )

    fireEvent.click(screen.getByRole("button", { name: "First: cloud" }))

    expect(screen.getByRole("button", { name: "First: local" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "Second: local" })).toBeTruthy()
    expect(
      window.localStorage.getItem("open-swe.agents.desktop-thread-source")
    ).toBe("local")
  })
})
