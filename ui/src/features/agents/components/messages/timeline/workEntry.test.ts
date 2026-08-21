import { describe, expect, it } from "vitest"

import { describeWorkEntry, liveActivityLabel } from "./workEntry"
import type { Chunk, DiffData, ToolExecutionChunk } from "@/lib/agentTypes"

const projectPath = "/workspace/open-swe"

function chunk(
  overrides: Partial<ToolExecutionChunk> = {}
): ToolExecutionChunk {
  return {
    kind: "tool-execution",
    toolCallId: "call_1",
    title: "read_file",
    toolKind: "read",
    status: "completed",
    ...overrides,
  }
}

function diff(overrides: Partial<DiffData> = {}): DiffData {
  return {
    filePath: `${projectPath}/ui/src/app.tsx`,
    originalContent: "a\n",
    newContent: "b\n",
    isNewFile: false,
    isBinary: false,
    isTruncated: false,
    totalLines: 1,
    ...overrides,
  }
}

describe("describeWorkEntry", () => {
  it("splits a read into a verb, file name, and full-path tooltip", () => {
    const fullPath = `${projectPath}/ui/src/AGENTS.md`
    const entry = describeWorkEntry(
      chunk({ input: { file_path: fullPath } }),
      projectPath
    )

    expect(entry.heading).toBe("Read")
    expect(entry.preview).toBe("AGENTS.md")
    expect(entry.previewTooltip).toBe(fullPath)
    expect(entry.icon).toBe("eye")
  })

  it("describes a completed edit from its diff rather than the raw tool title", () => {
    const entry = describeWorkEntry(
      chunk({ title: "edit_file", toolKind: "edit", diffData: diff() }),
      projectPath
    )

    expect(entry.heading).toBe("Edited")
    expect(entry.preview).toBe("app.tsx")
    expect(entry.previewTooltip).toBe(`${projectPath}/ui/src/app.tsx`)
    expect(entry.diffStats).toEqual({ additions: 1, deletions: 1 })
    expect(entry.icon).toBe("square-pen")
    // The diff is rendered as the row body, so there is no text fallback.
    expect(entry.expandedText).toBeNull()
  })

  it("distinguishes a created file from an edited one", () => {
    const entry = describeWorkEntry(
      chunk({ toolKind: "edit", diffData: diff({ isNewFile: true }) }),
      projectPath
    )

    expect(entry.heading).toBe("Created")
  })

  it("reports an in-flight edit in the present tense", () => {
    const entry = describeWorkEntry(
      chunk({ toolKind: "edit", status: "in_progress", diffData: diff() }),
      projectPath
    )

    expect(entry.heading).toBe("Editing")
    expect(entry.status).toBe("in_progress")
  })

  it("marks failed calls with the error tone", () => {
    const entry = describeWorkEntry(
      chunk({
        title: "shell",
        toolKind: "execute",
        status: "error",
        input: { command: "pnpm test" },
      }),
      projectPath
    )

    expect(entry.tone).toBe("error")
    expect(entry.icon).toBe("terminal")
    expect(entry.heading).toBe("Shell")
    expect(entry.preview).toBe("pnpm test")
  })

  it("drops a preview that would only repeat the heading", () => {
    const entry = describeWorkEntry(
      chunk({ title: "write_todos", toolKind: "other", input: {} }),
      projectPath
    )

    expect(entry.heading).toBe("Update todos")
    expect(entry.preview).toBeNull()
  })

  it("falls back to tool locations when the input carries no argument", () => {
    const entry = describeWorkEntry(
      chunk({
        title: "search",
        toolKind: "search",
        input: {},
        locations: [
          { path: `${projectPath}/a.ts` },
          { path: `${projectPath}/b.ts` },
        ],
      }),
      projectPath
    )

    expect(entry.preview).toBe("a.ts +1 more")
  })

  it("builds an expandable body from the command and its output", () => {
    const entry = describeWorkEntry(
      chunk({
        title: "shell",
        toolKind: "execute",
        input: { command: "ls" },
        output: "a.ts\nb.ts",
      }),
      projectPath
    )

    expect(entry.expandedText).toBe("ls\n\na.ts\nb.ts")
  })
})

describe("liveActivityLabel", () => {
  it("describes only the latest tool activity", () => {
    const chunks: Array<Chunk> = [
      { kind: "reasoning", text: "Inspecting" },
      chunk({
        toolCallId: "call_read",
        input: { file_path: `${projectPath}/ui/src/AGENTS.md` },
        status: "in_progress",
      }),
    ]

    expect(liveActivityLabel(chunks, projectPath)).toBe("Exploring · AGENTS.md")
  })

  it("switches to response status when final text starts streaming", () => {
    expect(
      liveActivityLabel([
        chunk({ toolKind: "execute", input: { command: "pnpm test" } }),
        { kind: "text", text: "The tests pass." },
      ])
    ).toBe("Writing response…")
  })

  it("surfaces approval and error states", () => {
    expect(liveActivityLabel([chunk({ status: "pending" })])).toBe(
      "Waiting for approval…"
    )
    expect(liveActivityLabel([chunk({ status: "error" })])).toBe(
      "Recovering from an error…"
    )
  })
})
