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

  it("falls back to a handle when a sender has no display name", () => {
    const introduction = `<dynamic-context kind="person" id="github:bob">
  <platform>github</platform>
  <github_login>bob</github_login>
</dynamic-context>`
    expect(parseStructuredInput(introduction)).toEqual({
      type: "entity",
      id: "github:bob",
      kind: "person",
      displayName: undefined,
      handle: "bob",
    })
    expect(collectStructuredEntities([introduction]).get("github:bob")).toEqual({
      kind: "person",
      displayName: undefined,
      handle: "bob",
    })
  })

  it("ignores the data fields a real envelope carries beside its content", () => {
    expect(
      parseStructuredInput(
        '<input-message sender="slack:U_ALICE" channel="slack:C_DEMO" surface="slack" kind="human">\n<timestamp>1787165487.000034</timestamp>\n<content>please add a greet() helper</content>\n</input-message>'
      )
    ).toEqual({
      type: "message",
      content: "please add a greet() helper",
      sender: "slack:U_ALICE",
      senderKind: "person",
    })
  })

  it("ignores nested data fields", () => {
    expect(
      parseStructuredInput(
        '<input-message sender="linear:dev@example.com" surface="linear" kind="human">\n<issue>\n<identifier>ENG-1</identifier>\n<labels>\n<item>bug</item>\n</labels>\n</issue>\n<content>Fix it</content>\n</input-message>'
      )
    ).toEqual({
      type: "message",
      content: "Fix it",
      sender: "linear:dev@example.com",
      senderKind: "person",
    })
  })

  it("falls back to legacy when the body holds unbalanced markup", () => {
    const content =
      '<input-message sender="github:alice" surface="web" kind="human">\n<timestamp>1\n<content>hi</content>\n</input-message>'
    expect(parseStructuredInput(content)).toEqual({ type: "legacy", content })
  })

  it("decodes person and system messages using safely derived entities", () => {
    const entities = collectStructuredEntities([person, system])

    expect(
      parseStructuredInput(
        '<input-message sender="github:alice" surface="web" kind="human">\n  <content>Hello &amp; welcome</content>\n</input-message>',
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
        '<input-message sender="system:scheduler" surface="automation"><content>Check CI</content></input-message>',
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
    const legacy = "Legacy <input-message> text & markdown"
    expect(parseStructuredInput(legacy)).toEqual({
      type: "legacy",
      content: legacy,
    })
    const malformed = '<input-message sender="github:alice"><content>incomplete'
    expect(parseStructuredInput(malformed)).toEqual({
      type: "legacy",
      content: malformed,
    })
  })
})
