import { describe, expect, it } from "vitest"

import {
  collectStructuredEntities,
  decodeXmlText,
  parseStructuredInput,
} from "./structuredInputMessages"

describe("structured input messages", () => {
  const person = `<dynamic-context kind="person" id="github:alice">
display_name: Alice &amp; Bob
</dynamic-context>`
  const system = `<dynamic-context kind="system" id="system:scheduler">
display_name: Scheduler
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
github_login: bob
</dynamic-context>`
    expect(parseStructuredInput(introduction)).toEqual({
      type: "entity",
      id: "github:bob",
      kind: "person",
      displayName: undefined,
      handle: "bob",
    })
    expect(collectStructuredEntities([introduction]).get("github:bob")).toEqual(
      {
        kind: "person",
        displayName: undefined,
        handle: "bob",
      }
    )
  })

  it("reads fields that follow an indented multi-line value", () => {
    const introduction = `<dynamic-context kind="person" id="user:1">
standing_instructions:
  Never use ripgrep.
  Prefer grep: display_name: not a field
github_login: carol
</dynamic-context>`
    expect(parseStructuredInput(introduction)).toEqual({
      type: "entity",
      id: "user:1",
      kind: "person",
      handle: "carol",
    })
  })

  it("still reads blocks stored with one element per field", () => {
    const introduction = `<dynamic-context kind="person" id="github:dave">
<display_name>Dave &amp; co</display_name>
<github_login>dave</github_login>
</dynamic-context>`
    expect(parseStructuredInput(introduction)).toEqual({
      type: "entity",
      id: "github:dave",
      kind: "person",
      displayName: "Dave & co",
      handle: "dave",
    })
  })

  it("takes the envelope's own text as the message", () => {
    expect(
      parseStructuredInput(
        '<input-message sender="slack:U_ALICE" channel="slack:C_DEMO" surface="slack" kind="human" timestamp="1787165487.000034">\nplease add a greet() helper\n</input-message>'
      )
    ).toEqual({
      type: "message",
      content: "please add a greet() helper",
      sender: "slack:U_ALICE",
      senderKind: "person",
      surface: "slack",
    })
  })

  it("still reads messages stored with a content element", () => {
    expect(
      parseStructuredInput(
        '<input-message sender="slack:U_ALICE" channel="slack:C_DEMO" surface="slack" kind="human">\n<timestamp>1787165487.000034</timestamp>\n<content>please add a greet() helper</content>\n</input-message>'
      )
    ).toEqual({
      type: "message",
      content: "please add a greet() helper",
      sender: "slack:U_ALICE",
      senderKind: "person",
      surface: "slack",
    })
  })

  it("hides a sender_context spliced in after the text", () => {
    expect(
      parseStructuredInput(
        '<input-message sender="slack:U_ALICE" surface="slack" kind="human">\nship it\n<sender_context>Git identity command: `git config user.name \'Alice\'`\n\nCo-authored-by: bot &lt;bot@example.com&gt;</sender_context>\n</input-message>'
      )
    ).toEqual({
      type: "message",
      content: "ship it",
      sender: "slack:U_ALICE",
      senderKind: "person",
      surface: "slack",
    })
  })

  it("ignores nested data fields", () => {
    expect(
      parseStructuredInput(
        '<input-message sender="linear:dev@example.com" surface="linear" kind="human">\nFix it\n<issue>\n<identifier>ENG-1</identifier>\n<labels>\n<item>bug</item>\n</labels>\n</issue>\n</input-message>'
      )
    ).toEqual({
      type: "message",
      content: "Fix it",
      sender: "linear:dev@example.com",
      senderKind: "person",
      surface: "linear",
    })
  })

  it("falls back to legacy when the body holds unbalanced markup", () => {
    const stored =
      '<input-message sender="github:alice" surface="web" kind="human">\n<timestamp>1\n<content>hi</content>\n</input-message>'
    expect(parseStructuredInput(stored)).toEqual({
      type: "legacy",
      content: stored,
    })
    const content =
      '<input-message sender="github:alice" surface="web" kind="human">\nhi\n<timestamp>1\n</input-message>'
    expect(parseStructuredInput(content)).toEqual({ type: "legacy", content })
  })

  it("decodes person and system messages using safely derived entities", () => {
    const entities = collectStructuredEntities([person, system])

    expect(
      parseStructuredInput(
        '<input-message sender="github:alice" surface="web" kind="human">\nHello &amp; welcome\n</input-message>',
        entities
      )
    ).toEqual({
      type: "message",
      content: "Hello & welcome",
      sender: "github:alice",
      senderKind: "person",
      surface: "web",
    })
    expect(
      parseStructuredInput(
        '<input-message sender="system:scheduler" surface="automation">\nCheck CI\n</input-message>',
        entities
      )
    ).toEqual({
      type: "message",
      content: "Check CI",
      sender: "system:scheduler",
      senderKind: "system",
      surface: "automation",
    })
  })

  it("carries the bot marker and account link status of an entity", () => {
    const bot = `<dynamic-context kind="system" id="system:slack-bot-B9">
display_name: CI Bot
sender_type: bot
</dynamic-context>`
    const guest = `<dynamic-context kind="person" id="slack:U456">
display_name: Guest
open_swe_account: unlinked
</dynamic-context>`
    const entities = collectStructuredEntities([bot, guest])

    expect(entities.get("system:slack-bot-B9")?.senderType).toBe("bot")
    expect(entities.get("slack:U456")?.openSweAccount).toBe("unlinked")
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
