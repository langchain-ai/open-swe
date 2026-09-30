import { expect, it } from "vitest"
import { chatDiffAction } from "./chatDiffActions"

const range = { file: "a.py", start_line: 3, end_line: 5, side: "RIGHT" }

it("reads a drafted comment and review", () => {
  expect(
    chatDiffAction({
      type: "tool",
      name: "propose_review_comment",
      tool_call_id: "c1",
      content: [
        {
          type: "text",
          text: JSON.stringify({ proposed: true, range, body: "Nit" }),
        },
      ],
    })
  ).toEqual({
    kind: "comment",
    id: "c1",
    range: { file: "a.py", startLine: 3, endLine: 5, side: "RIGHT" },
    body: "Nit",
  })
  expect(
    chatDiffAction({
      type: "tool",
      name: "propose_pr_review",
      tool_call_id: "c2",
      content: JSON.stringify({ proposed: true, event: "APPROVE", body: "" }),
    })
  ).toMatchObject({ kind: "review", id: "c2", event: "APPROVE" })
})

it("ignores rejected drafts and other tools", () => {
  expect(
    chatDiffAction({
      type: "tool",
      name: "propose_review_comment",
      tool_call_id: "c3",
      content: JSON.stringify({ proposed: false, error: "bad range" }),
    })
  ).toBeNull()
  expect(
    chatDiffAction({
      type: "tool",
      name: "read_repo_file",
      tool_call_id: "c4",
      content: JSON.stringify({ proposed: true, range, body: "x" }),
    })
  ).toBeNull()
})
