/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { Markdown } from "./Markdown"

vi.mock("@/features/agents/components/PullRequestPreview", () => ({
  PreviewablePullRequestLink: (props: React.ComponentProps<"a">) => (
    <a {...props} />
  ),
}))

const attachment =
  "https://github.com/user-attachments/assets/a1e89096-2c71-48cb-b93e-7559b9667c64"

afterEach(cleanup)

it("embeds a standalone GitHub attachment and restores its link if playback fails", () => {
  render(<Markdown content={attachment} />)

  const video = screen.getByLabelText("GitHub attachment video")
  expect(video.getAttribute("src")).toBe(attachment)
  expect(video.hasAttribute("controls")).toBe(true)
  fireEvent.error(video)
  expect(screen.queryByLabelText("GitHub attachment video")).toBeNull()
  expect(
    screen.getByRole("link", { name: attachment }).getAttribute("href")
  ).toBe(attachment)
})

it.each([
  `[Download recording](${attachment})`,
  `See ${attachment} for details.`,
  "https://example.com/user-attachments/assets/a1e89096-2c71-48cb-b93e-7559b9667c64",
  `\`${attachment}\``,
])(
  "keeps non-standalone attachments, other hosts, and code unchanged: %s",
  (content) => {
    render(<Markdown content={content} />)
    expect(screen.queryByLabelText("GitHub attachment video")).toBeNull()
  }
)
