import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

import { WorkEntryRow } from "./WorkEntryRow"
import type { WorkEntryView } from "./workEntry"

function entry(elapsedMs?: number): WorkEntryView {
  return {
    icon: "wrench",
    heading: "Called tool",
    preview: null,
    tone: "tool",
    status: "completed",
    elapsedMs,
    expandedText: null,
  }
}

describe("WorkEntryRow", () => {
  it("shows persisted durations at or above one second", () => {
    const html = renderToStaticMarkup(<WorkEntryRow entry={entry(2345)} />)

    expect(html).toContain("took 3s")
  })

  it("hides sub-second and missing durations", () => {
    expect(
      renderToStaticMarkup(<WorkEntryRow entry={entry(999)} />)
    ).not.toContain("took")
    expect(
      renderToStaticMarkup(<WorkEntryRow entry={entry()} />)
    ).not.toContain("took")
  })
})
