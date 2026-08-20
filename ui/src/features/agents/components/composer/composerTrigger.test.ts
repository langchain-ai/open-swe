import { describe, expect, it } from "vitest"

import {
  collectComposerMentions,
  splitPromptIntoSegments,
} from "./composerMentions"
import {
  detectComposerTrigger,
  replaceTextRange,
  serializeComposerFileLink,
} from "./composerTrigger"

describe("detectComposerTrigger", () => {
  it("opens the file menu on a bare @ and tracks the query", () => {
    expect(detectComposerTrigger("look at @", 9)).toEqual({
      kind: "path",
      query: "",
      rangeStart: 8,
      rangeEnd: 9,
    })
    expect(detectComposerTrigger("look at @src/app", 16)).toMatchObject({
      kind: "path",
      query: "src/app",
    })
  })

  it("ignores an @ that is part of a longer token", () => {
    expect(detectComposerTrigger("mail me@example.com", 19)).toBeNull()
  })

  it("treats a whitespace-delimited slash token as a command anywhere", () => {
    expect(detectComposerTrigger("/pl", 3)).toEqual({
      kind: "slash-command",
      query: "pl",
      rangeStart: 0,
      rangeEnd: 3,
    })
    expect(detectComposerTrigger("fix with /review-pr please", 19)).toEqual({
      kind: "slash-command",
      query: "review-pr",
      rangeStart: 9,
      rangeEnd: 19,
    })
    expect(detectComposerTrigger("see src/a/b", 11)).toBeNull()
  })

  it("treats a whitespace-delimited dollar token as a skill picker", () => {
    expect(detectComposerTrigger("use $baby-sit", 13)).toEqual({
      kind: "skill-command",
      query: "baby-sit",
      rangeStart: 4,
      rangeEnd: 13,
    })
  })

  it("closes the trigger once the cursor moves off the token", () => {
    expect(detectComposerTrigger("@src/app.tsx done", 17)).toBeNull()
  })

  it("clamps an out-of-range cursor instead of reading past the text", () => {
    expect(detectComposerTrigger("@app", 999)).toMatchObject({ query: "app" })
    expect(detectComposerTrigger("@app", -5)).toBeNull()
  })
})

describe("replaceTextRange", () => {
  it("splices the replacement in and reports the cursor after it", () => {
    expect(
      replaceTextRange("look at @src", 8, 12, "[a.tsx](src/a.tsx) ")
    ).toEqual({
      text: "look at [a.tsx](src/a.tsx) ",
      cursor: 27,
    })
  })
})

describe("serializeComposerFileLink", () => {
  it("labels with the basename and encodes the path", () => {
    expect(serializeComposerFileLink("ui/src/app.tsx")).toBe(
      "[app.tsx](ui/src/app.tsx)"
    )
    expect(serializeComposerFileLink("ui/my file (1).tsx")).toBe(
      "[my file (1).tsx](ui/my%20file%20%281%29.tsx)"
    )
  })
})

describe("collectComposerMentions", () => {
  it("recognises both markdown links and bare @paths, in document order", () => {
    const mentions = collectComposerMentions(
      "see [a.tsx](src/a.tsx) and @src/b.ts too"
    )
    expect(mentions.map((m) => m.path)).toEqual(["src/a.tsx", "src/b.ts"])
  })

  it("leaves ordinary links and external URLs alone", () => {
    expect(collectComposerMentions("read [the docs](src/a.tsx)")).toEqual([])
    expect(
      collectComposerMentions("[example.com](https://example.com)")
    ).toEqual([])
  })

  it("round-trips a mention through segments", () => {
    const prompt = "fix [app.tsx](ui/src/app.tsx) please"
    const segments = splitPromptIntoSegments(prompt)
    expect(segments).toEqual([
      { type: "text", text: "fix " },
      {
        type: "mention",
        path: "ui/src/app.tsx",
        source: "[app.tsx](ui/src/app.tsx)",
      },
      { type: "text", text: " please" },
    ])
    expect(
      segments.map((s) => (s.type === "text" ? s.text : s.source)).join("")
    ).toBe(prompt)
  })
})
