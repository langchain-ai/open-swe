import { describe, expect, it } from "vitest"

import {
  collectStructuredEntities,
  decodeXmlText,
  parseStructuredInput,
} from "./structuredInputMessages"

describe("structured input messages", () => {
  const person = `<dynamic-context kind="person" id="github:alice">
  <display_name>Alice &amp; Bob</display_name>
</dynamic-context>`
  const system = `<dynamic-context kind="system" id="system:scheduler">
  <display_name>Scheduler</display_name>
</dynamic-context>`

  it("recognizes entity introductions so transcripts can hide them", () => {
    expect(parseStructuredInput(person)).toEqual({
      type: "entity",
      id: "github:alice",
      kind: "person",
      displayName: "Alice & Bob",
    })
  })

  it("decodes person and system messages using safely derived entities", () => {
    const entities = collectStructuredEntities([person, system])

    expect(
      parseStructuredInput(
        '<chat-message sender="github:alice" surface="web" kind="human">\n  <content>Hello &amp; welcome</content>\n</chat-message>',
        entities
      )
    ).toEqual({
      type: "message",
      content: "Hello & welcome",
      sender: "github:alice",
      senderKind: "person",
    })
    expect(
      parseStructuredInput(
        '<chat-message sender="system:scheduler" surface="automation"><content>Check CI</content></chat-message>',
        entities
      )
    ).toEqual({
      type: "message",
      content: "Check CI",
      sender: "system:scheduler",
      senderKind: "system",
    })
  })

  it("decodes escaped markup as plain text and supports numeric entities", () => {
    expect(
      decodeXmlText("&lt;img src=x onerror=alert(1)&gt; &#x26; &#38;")
    ).toBe("<img src=x onerror=alert(1)> & &")
  })

  it("leaves malformed and legacy messages unchanged", () => {
    const legacy = "Legacy <chat-message> text & markdown"
    expect(parseStructuredInput(legacy)).toEqual({
      type: "legacy",
      content: legacy,
    })
    const malformed = '<chat-message sender="github:alice"><content>incomplete'
    expect(parseStructuredInput(malformed)).toEqual({
      type: "legacy",
      content: malformed,
    })
  })
})
