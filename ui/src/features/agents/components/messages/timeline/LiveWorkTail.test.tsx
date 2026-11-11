/** @vitest-environment jsdom */

import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { WorkTail, buildRenderItems } from "../renderItems"
import { LiveWorkTail } from "./LiveWorkTail"
import type { Chunk } from "@/features/agents/lib/types"

afterEach(() => cleanup())

describe("LiveWorkTail", () => {
  it("shows the latest thought and lets the latest shell output expand on demand", async () => {
    const loadOutput = vi.fn().mockResolvedValue("Focused tests passed")
    const chunks: Array<Chunk> = [
      { kind: "reasoning", text: "An earlier thought" },
      {
        kind: "tool-execution",
        toolCallId: "old",
        title: "read_file",
        toolKind: "read",
        status: "completed",
      },
      { kind: "reasoning", text: "Checking the focused tests" },
      {
        kind: "tool-execution",
        toolCallId: "latest",
        title: "execute",
        toolKind: "execute",
        status: "in_progress",
        input: { command: "uv run pytest tests/human_review" },
        loadOutput,
      },
    ]
    const { rerender } = render(
      <LiveWorkTail tail={new WorkTail(buildRenderItems(chunks))} />,
      { wrapper: TooltipProvider }
    )

    expect(screen.queryByText("An earlier thought")).toBeNull()
    expect(screen.getByText("Checking the focused tests")).toBeTruthy()
    const shell = screen.getByRole("button", { name: /Shell uv run pytest/ })
    expect(shell.getAttribute("aria-expanded")).toBe("false")
    expect(loadOutput).not.toHaveBeenCalled()
    fireEvent.keyDown(shell, { key: "Enter" })
    await waitFor(() =>
      expect(screen.getByText("Focused tests passed")).toBeTruthy()
    )
    expect(shell.getAttribute("aria-expanded")).toBe("true")

    rerender(
      <LiveWorkTail
        tail={
          new WorkTail(
            buildRenderItems([
              ...chunks,
              {
                kind: "tool-execution",
                toolCallId: "next",
                title: "execute",
                toolKind: "execute",
                status: "in_progress",
                input: { command: "git diff --check" },
              },
            ])
          )
        }
      />
    )
    expect(screen.queryByText("Focused tests passed")).toBeNull()
    expect(
      screen
        .getByRole("button", { name: /Shell git diff/ })
        .getAttribute("aria-expanded")
    ).toBe("false")
  })

  it.each(["completed", "error"] as const)(
    "expands %s edits without hiding output or waiting indefinitely",
    async (status) => {
      const loadOutput = vi.fn().mockResolvedValue("Full edit output")
      render(
        <LiveWorkTail
          tail={
            new WorkTail(
              buildRenderItems([
                {
                  kind: "tool-execution",
                  toolCallId: "edit",
                  title: "edit_file",
                  toolKind: "edit",
                  status,
                  output: "Edit output preview",
                  loadOutput,
                  diffData: {
                    filePath: "greet.ts",
                    originalContent: "hello",
                    newContent: "goodbye",
                    isNewFile: false,
                    isBinary: false,
                    isTruncated: false,
                    totalLines: 1,
                  },
                },
              ])
            )
          }
        />,
        { wrapper: TooltipProvider }
      )

      const edit = screen.getByRole("button")
      expect(edit.getAttribute("aria-expanded")).toBe("false")
      fireEvent.click(edit)
      expect(edit.getAttribute("aria-expanded")).toBe("true")
      await waitFor(() =>
        expect(loadOutput).toHaveBeenCalledTimes(status === "error" ? 1 : 0)
      )
      expect(screen.queryAllByText("greet.ts").length).toBe(
        status === "error" ? 0 : 2
      )
      await waitFor(() =>
        expect(screen.queryByText(/Loading.*output/)).toBeNull()
      )
      expect(Boolean(screen.queryByText("Full edit output"))).toBe(
        status === "error"
      )
    }
  )
})
