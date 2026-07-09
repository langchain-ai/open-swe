import { AIMessage } from "@langchain/core/messages"
import { expect, it } from "vitest"

import { streamMessagesToUi } from "./streamMessagesToUi"

it("omits write_todos calls from transcript work", () => {
  const message = new AIMessage({
    content: "",
    tool_calls: [
      {
        id: "tool-todos",
        name: "write_todos",
        args: {
          todos: [{ content: "Render todos", status: "in_progress" }],
        },
        type: "tool_call",
      },
    ],
  })

  expect(streamMessagesToUi([message])).toEqual([])
})
