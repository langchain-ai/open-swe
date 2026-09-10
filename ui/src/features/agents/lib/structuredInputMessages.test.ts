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
    expect(collectStructuredEntities([introduction]).get("github:bob")).toEqual(
      {
        kind: "person",
        displayName: undefined,
        handle: "bob",
      }
    )
  })

  it("surfaces media data items as attachments served from the sandbox", () => {
    const sha = "a".repeat(64)
    const envelope = [
      '<input-message sender="github:alice" surface="web" kind="human">',
      "<media>",
      "<item>",
      `<path>/workspace/.open-swe-media/${sha}.png</path>`,
      "<mime_type>image/png</mime_type>",
      `<sha256>${sha}</sha256>`,
      "<size>3</size>",
      "<file_name>Screen &amp; shot.png</file_name>",
      "</item>",
      "</media>",
      "<content>see attached</content>",
      "</input-message>",
    ].join("\n")

    expect(parseStructuredInput(envelope)).toEqual({
      type: "message",
      content: "see attached",
      sender: "github:alice",
      senderKind: "person",
      surface: "web",
      attachments: [
        {
          name: `${sha}.png`,
          mimeType: "image/png",
          fileName: "Screen & shot.png",
        },
      ],
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
      surface: "slack",
    })
  })

  it("hides a sender_context that follows the content element", () => {
    expect(
      parseStructuredInput(
        '<input-message sender="slack:U_ALICE" surface="slack" kind="human">\n<content>ship it</content>\n<sender_context>Git identity command: `git config user.name \'Alice\'`\n\nCo-authored-by: bot &lt;bot@example.com&gt;</sender_context>\n</input-message>'
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
        '<input-message sender="linear:dev@example.com" surface="linear" kind="human">\n<issue>\n<identifier>ENG-1</identifier>\n<labels>\n<item>bug</item>\n</labels>\n</issue>\n<content>Fix it</content>\n</input-message>'
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
      surface: "web",
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
      surface: "automation",
    })
  })

  it("carries the bot marker and account link status of an entity", () => {
    const bot = `<dynamic-context kind="system" id="system:slack-bot-B9">
  <display_name>CI Bot</display_name>
  <sender_type>bot</sender_type>
</dynamic-context>`
    const guest = `<dynamic-context kind="person" id="slack:U456">
  <display_name>Guest</display_name>
  <open_swe_account>unlinked</open_swe_account>
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
