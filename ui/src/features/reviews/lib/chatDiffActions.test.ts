import { expect, it } from "vitest"
import { chatDiffAction } from "./chatDiffActions"

const range = { file: "a.py", start_line: 3, end_line: 5, side: "RIGHT" }

it("reads successful show and comment results", () => {
  expect(
    chatDiffAction({
      type: "tool",
      name: "show_in_diff",
      tool_call_id: "c1",
      content: JSON.stringify({ shown: true, range }),
    })
  ).toEqual({
    kind: "show",
    id: "c1",
    range: { file: "a.py", startLine: 3, endLine: 5, side: "RIGHT" },
  })
  expect(
    chatDiffAction({
      type: "tool",
      name: "propose_review_comment",
      tool_call_id: "c2",
      content: [
        {
          type: "text",
          text: JSON.stringify({ proposed: true, range, body: "Nit" }),
        },
      ],
    })
  ).toMatchObject({ kind: "comment", id: "c2", body: "Nit" })
})

it("ignores failed calls and other tools", () => {
  expect(
    chatDiffAction({
      type: "tool",
      name: "show_in_diff",
      tool_call_id: "c3",
      content: JSON.stringify({ shown: false, error: "bad range" }),
    })
  ).toBeNull()
  expect(
    chatDiffAction({
      type: "tool",
      name: "read_repo_file",
      tool_call_id: "c4",
      content: JSON.stringify({ shown: true, range }),
    })
  ).toBeNull()
})
