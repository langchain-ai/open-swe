/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { OutputIframe } from "./OutputIframe"

const display = {
  type: "output_iframe" as const,
  previewUrl: "https://downloads.example/preview?token=secret",
  downloadUrl: "https://downloads.example/download?token=secret",
  title: "Iframe preview",
  filename: "preview.html",
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("OutputIframe", () => {
  it("renders the signed preview URL in a sandboxed iframe", () => {
    render(<OutputIframe display={display} />)

    const iframe = screen.getByTitle(display.title)
    expect(iframe.getAttribute("src")).toBe(display.previewUrl)
    expect(iframe.getAttribute("srcdoc")).toBeNull()
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts allow-downloads")
    expect(iframe.getAttribute("referrerpolicy")).toBe("no-referrer")
    expect(screen.queryByRole("button", { name: "Open in new tab" })).toBeNull()
  })

  it("renders historical HTML artifacts without blob actions", () => {
    render(
      <OutputIframe
        display={{
          type: "output_iframe",
          html: "<h1>Historical preview</h1>",
          title: "Historical preview",
          filename: "preview.html",
        }}
      />
    )

    const iframe = screen.getByTitle("Historical preview")
    expect(iframe.getAttribute("srcdoc")).toBe("<h1>Historical preview</h1>")
    expect(iframe.getAttribute("src")).toBeNull()
    expect(screen.queryByRole("button", { name: "Download HTML" })).toBeNull()
  })

  it("downloads through the signed attachment URL", () => {
    let clickedHref = ""
    let clickedRel = ""
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      function (this: HTMLAnchorElement) {
        clickedHref = this.href
        clickedRel = this.rel
      }
    )
    render(<OutputIframe display={display} />)

    fireEvent.click(screen.getByRole("button", { name: "Download HTML" }))

    expect(clickedHref).toBe(display.downloadUrl)
    expect(clickedRel).toBe("noreferrer")
  })
})
