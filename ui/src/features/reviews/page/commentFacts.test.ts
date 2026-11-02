import { describe, expect, it } from "vitest"

import type { ConversationItem } from "@/features/reviews/lib/conversationApi"
import { commentFacts } from "./commentFacts"

function comment(
  id: number,
  created_at: string,
  body: string
): ConversationItem {
  return { kind: "comment", id, author: null, created_at, body, html_url: "" }
}

const preview = (url: string) => `## Frontend Preview Environment

| Component | Status | Link |
|-----------|--------|------|
| **Build** | ✅ Built \`preview-7dc349f\` | [View Build](https://example.com/run) |

## Ingress URLs

| Service | URL |
|---------|-----|
| **Frontend** | [${url}](${url}) |
| Plain | ignored |
`

describe("commentFacts", () => {
  it("reads bold-keyed table rows, letting the latest comment win", () => {
    expect(
      commentFacts([
        comment(2, "2026-10-30T20:00:00Z", preview("https://new")),
        comment(1, "2026-10-30T19:00:00Z", preview("https://old")),
        comment(
          3,
          "2026-10-30T21:00:00Z",
          "```\n| **Code** | skipped |\n|---|---|\n```"
        ),
      ])
    ).toEqual([
      {
        key: "Build",
        value:
          "✅ Built `preview-7dc349f` · [View Build](https://example.com/run)",
      },
      { key: "Frontend", value: "[https://new](https://new)" },
    ])
  })
})
