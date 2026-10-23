import { AIMessage, HumanMessage } from "@langchain/core/messages"
import { describe, expect, it } from "vitest"

import {
  NETWORK_RESUME_SENDER,
  classifyStreamFailure,
  mergeNetworkRecoveryAttempts,
} from "./networkRecovery"
import { streamMessagesToUi } from "./streamMessagesToUi"

function resumeInput(text = "continue the task"): HumanMessage {
  return new HumanMessage({
    id: `resume-${text}`,
    content: `<input-message sender="${NETWORK_RESUME_SENDER}" surface="desktop" kind="system">\n<content>${text}</content>\n</input-message>`,
  })
}

function user(text: string): HumanMessage {
  return new HumanMessage({ id: `user-${text}`, content: text })
}

function agent(text: string, id = `ai-${text}`): AIMessage {
  return new AIMessage({ id, content: text })
}

describe("classifyStreamFailure", () => {
  it("treats transport failures as recoverable", () => {
    expect(
      classifyStreamFailure({ online: false, message: "x", status: null })
    ).toBe("network")
    expect(
      classifyStreamFailure({
        online: true,
        message: "TypeError: fetch failed",
        status: null,
      })
    ).toBe("network")
    expect(
      classifyStreamFailure({
        online: true,
        message: "Event stream closed by the server",
        status: null,
      })
    ).toBe("network")
  })

  it("leaves a server answer alone", () => {
    expect(
      classifyStreamFailure({ online: true, message: "forbidden", status: 403 })
    ).toBeNull()
    expect(classifyStreamFailure({ online: true, message: null, status: null }))
      .toBeNull()
  })
})

describe("mergeNetworkRecoveryAttempts", () => {
  it("keeps a single attempt untouched", () => {
    const merge = mergeNetworkRecoveryAttempts([user("fix it"), agent("done")])

    expect(merge.messages).toHaveLength(2)
    expect(merge.continued.size).toBe(0)
  })

  it("marks the attempt that continues an interrupted one", () => {
    const merge = mergeNetworkRecoveryAttempts([
      user("fix it"),
      agent("part one"),
      resumeInput(),
      agent("part two"),
    ])

    expect(merge.messages.map((message) => message.text)).toEqual([
      "fix it",
      "part one",
      "part two",
    ])
    expect([...merge.continued]).toEqual([2])
  })
})

describe("streamMessagesToUi with network recovery", () => {
  it("shows one question and one continuous answer", () => {
    const messages = streamMessagesToUi([
      user("fix the failing test"),
      agent("Investigated the suite."),
      resumeInput(),
      agent("Patched the assertion and it passes."),
    ])

    expect(messages).toHaveLength(2)
    expect(messages[0]).toMatchObject({ author: "user" })
    expect(messages[1]).toMatchObject({
      author: "agent",
      chunks: [
        {
          kind: "text",
          text: "Investigated the suite.\n\nPatched the assertion and it passes.",
        },
      ],
    })
  })

  it("keeps the answer continuous across three attempts", () => {
    const messages = streamMessagesToUi([
      user("ship it"),
      agent("one"),
      resumeInput("first"),
      agent("two"),
      resumeInput("second"),
      agent("three"),
    ])

    expect(messages).toHaveLength(2)
    expect(messages[1]?.chunks).toEqual([
      { kind: "text", text: "one\n\ntwo\n\nthree" },
    ])
  })

  it("survives an attempt that produced nothing", () => {
    const messages = streamMessagesToUi([
      user("ship it"),
      agent("one"),
      resumeInput("first"),
      resumeInput("second"),
      agent("two"),
    ])

    expect(messages).toHaveLength(2)
    expect(messages[1]?.chunks).toEqual([{ kind: "text", text: "one\n\ntwo" }])
  })
})
