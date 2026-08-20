import { describe, expect, it } from "vitest"

import {
  formatToolDisplay,
  formatToolDisplayParts,
} from "./toolExecutionDisplay"

describe("formatToolDisplay", () => {
  const projectPath = "/workspace/open-swe"

  it("renders only the read file name and retains its full path", () => {
    const fullPath = "/workspace/open-swe/ui/src/AGENTS.md"

    expect(
      formatToolDisplay(
        `read_file ${fullPath}`,
        "read",
        { file_path: fullPath },
        projectPath
      )
    ).toBe("Read AGENTS.md")
    expect(
      formatToolDisplayParts(
        `read_file ${fullPath}`,
        "read",
        { file_path: fullPath },
        projectPath
      )
    ).toEqual({
      heading: "Read",
      preview: "AGENTS.md",
      previewTooltip: fullPath,
    })
  })

  it("renders ls with only the target directory name", () => {
    expect(
      formatToolDisplay(
        "ls /workspace/open-swe/ui/src",
        "read",
        { path: "/workspace/open-swe/ui/src" },
        projectPath
      )
    ).toBe("List src")
  })

  it("renders search tools with their pattern", () => {
    expect(
      formatToolDisplay(
        "grep",
        "search",
        { pattern: "tool_calls" },
        projectPath
      )
    ).toBe('Search "tool_calls"')
  })

  it("normalizes write_todos", () => {
    expect(formatToolDisplay("write todos", "other", {}, projectPath)).toBe(
      "Update todos"
    )
  })

  it("sentence-cases raw tool names", () => {
    expect(formatToolDisplay("enter_plan_mode", "other", {}, projectPath)).toBe(
      "Enter plan mode"
    )
    expect(formatToolDisplay("save_plan", "other", {}, projectPath)).toBe(
      "Save plan"
    )
    expect(
      formatToolDisplay("slack_thread_reply", "other", {}, projectPath)
    ).toBe("Slack thread reply")
  })
})
