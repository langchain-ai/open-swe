/** @vitest-environment jsdom */
import { describe, expect, it } from "vitest"

import { openExternalLinksInNewWindow } from "./external-links"

function clickEvent(target: Element): MouseEvent {
  const event = new MouseEvent("click")
  Object.defineProperty(event, "target", { value: target })
  return event
}

describe("openExternalLinksInNewWindow", () => {
  it("opens external links in a new window", () => {
    const anchor = document.createElement("a")
    anchor.href = "https://github.com/langchain-ai/open-swe"
    const child = document.createElement("span")
    anchor.append(child)

    openExternalLinksInNewWindow(clickEvent(child))

    expect(anchor.target).toBe("_blank")
    expect(anchor.rel).toBe("noopener noreferrer")
  })

  it("leaves internal links in the current window", () => {
    const anchor = document.createElement("a")
    anchor.href = "/agents"

    openExternalLinksInNewWindow(clickEvent(anchor))

    expect(anchor.target).toBe("")
  })

  it("preserves an explicit target", () => {
    const anchor = document.createElement("a")
    anchor.href = "https://github.com/langchain-ai/open-swe"
    anchor.target = "review"

    openExternalLinksInNewWindow(clickEvent(anchor))

    expect(anchor.target).toBe("review")
    expect(anchor.rel).toBe("")
  })
})
