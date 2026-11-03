import { describe, expect, it } from "vitest"

import { quoteFromPage, withAttachments } from "./ComposerAttachments"

describe("withAttachments", () => {
  it("folds every card into what the agent receives", () => {
    const image = {
      kind: "image" as const,
      base64: "AA==",
      mimeType: "image/png",
    }
    const reply = {
      source: "conversation" as const,
      before: "",
      selection: "Opening a pull request.",
      after: "",
      comment: "Which branch?",
    }
    const submission = withAttachments(
      "Thanks!",
      [],
      [
        { kind: "reply", reply },
        { kind: "image", image, comment: "Make this smaller" },
        quoteFromPage({
          label: "Fix",
          quote: "Off by one",
          comment: "Fix the finding.",
        }),
        {
          kind: "code",
          excerpt: {
            path: "a.py",
            lineLabel: "R2",
            language: "py",
            snippet: "> 2 | B = 2",
          },
        },
      ]
    )

    expect(submission.replies).toEqual([
      reply,
      {
        source: "conversation",
        before: "",
        selection: "Off by one",
        after: "",
        comment: "Fix the finding.",
      },
    ])
    expect(submission.images).toEqual([image])
    expect(submission.text).toBe(
      "`a.py:R2`\n```py\n> 2 | B = 2\n```\n\nMake this smaller\n\nThanks!"
    )
  })
})
