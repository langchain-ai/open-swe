/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { afterEach, expect, it, vi } from "vitest"

import { Markdown } from "./Markdown"

vi.mock("@/features/agents/components/PullRequestPreview", () => ({
  PreviewablePullRequestLink: (props: React.ComponentProps<"a">) => (
    <a {...props} />
  ),
  PreviewablePullRequestCard: ({ fallback }: { fallback: React.ReactNode }) =>
    fallback,
}))

const attachment =
  "https://github.com/user-attachments/assets/a1e89096-2c71-48cb-b93e-7559b9667c64"

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it("expands a table without removing its inline copy and returns focus on Escape", async () => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    }
  )
  render(
    <Markdown
      content={
        "| Finding | Evidence |\n| --- | --- |\n| Queue | Worker saturation |"
      }
    />,
    {
      wrapper: TooltipProvider,
    }
  )

  const trigger = screen.getByRole("button", { name: "Expand table" })
  trigger.focus()
  fireEvent.click(trigger)
  const dialog = screen.getByRole("dialog", { name: "Table" })
  expect(
    within(dialog).getByRole("cell", { name: "Worker saturation" })
  ).not.toBeNull()
  expect(screen.getAllByRole("table", { hidden: true })).toHaveLength(2)

  fireEvent.keyDown(dialog, { key: "Escape" })
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
  expect(screen.getAllByRole("table")).toHaveLength(1)
  expect(document.activeElement).toBe(trigger)
})

it("embeds a standalone GitHub attachment and restores its link if playback fails", () => {
  render(
    <Markdown
      content={attachment}
      transformMediaUrl={(src) => `/proxy?url=${encodeURIComponent(src)}`}
    />
  )

  const video = screen.getByLabelText("GitHub attachment video")
  // A private repo's upload only plays through the authenticated proxy.
  expect(video.getAttribute("src")).toBe(
    `/proxy?url=${encodeURIComponent(attachment)}`
  )
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
