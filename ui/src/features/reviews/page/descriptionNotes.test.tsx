/** @vitest-environment jsdom */

import { cleanup, render } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { Markdown } from "@/features/agents/components/chat/Markdown"
import { NOTE_ALLOWED_TAGS, remarkDescriptionNotes } from "./descriptionNotes"

vi.mock("@/features/agents/components/PullRequestPreview", () => ({
  PreviewablePullRequestLink: (props: React.ComponentProps<"a">) => (
    <a {...props} />
  ),
}))

afterEach(cleanup)

function marks(content: string, sentences: Array<string>) {
  const { container } = render(
    <Markdown
      content={content}
      remarkPlugins={[[remarkDescriptionNotes, sentences]]}
      allowedTags={NOTE_ALLOWED_TAGS}
    />
  )
  return [...container.querySelectorAll("[data-description-note]")].map(
    (span) => [
      span.getAttribute("data-description-note"),
      span.textContent,
      span.hasAttribute("data-description-note-end"),
    ]
  )
}

it("wraps a quoted sentence however code, emphasis and links split it", () => {
  expect(
    marks(
      "Intro. Adds `Plan.description` to **every** [plan](https://x.y), carried\nacross heads. Unrelated.",
      [
        "Adds `Plan.description` to **every** [plan](https://x.y), carried across heads.",
        "Not in the description.",
      ]
    )
  ).toEqual([
    ["0", "Adds ", false],
    ["0", "Plan.description", false],
    ["0", " to ", false],
    ["0", "every", false],
    ["0", " ", false],
    ["0", "plan", false],
    ["0", ", carried\nacross heads.", true],
  ])
})

it("finds list items quoted with their marker and leaves the rest alone", () => {
  expect(
    marks("- First change.\n- Second change.", ["- Second change."])
  ).toEqual([["0", "Second change.", true]])
})
