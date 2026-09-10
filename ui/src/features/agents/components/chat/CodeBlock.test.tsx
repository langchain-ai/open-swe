/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { CodeBlock } from "./CodeBlock"
import { TooltipProvider } from "@/components/ui/tooltip"

vi.mock("@/lib/theme", () => ({ useResolvedTheme: () => "light" }))

afterEach(cleanup)

it("copies code without trimming indentation, trailing spaces, or content blank lines", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  })
  render(
    <TooltipProvider>
      <CodeBlock text={"  keep spaces  \n\n\n"} />
    </TooltipProvider>
  )

  fireEvent.click(screen.getByRole("button", { name: "Copy code" }))

  await waitFor(() => {
    expect(writeText).toHaveBeenCalledWith("  keep spaces  \n\n")
    expect(screen.getByRole("button", { name: "Copied" })).toBeTruthy()
  })
  fireEvent.click(screen.getByRole("button", { name: "Wrap lines" }))
  expect(
    screen
      .getByRole("button", { name: "Disable line wrap" })
      .getAttribute("aria-pressed")
  ).toBe("true")
})
