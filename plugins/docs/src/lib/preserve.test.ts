import { Editor } from "@tiptap/core"
import Image from "@tiptap/extension-image"
import { TaskItem, TaskList } from "@tiptap/extension-list"
import { TableKit } from "@tiptap/extension-table"
import { Markdown } from "@tiptap/markdown"
import StarterKit from "@tiptap/starter-kit"
import { describe, expect, it } from "vitest"

import { asLiveMarkdown } from "@/lib/live"
import { DiagramCodeBlock } from "@/lib/mermaid"
import { preserveFormatting } from "@/lib/preserve"

const editor = new Editor({
  element: null,
  content: { type: "doc", content: [{ type: "paragraph" }] },
  extensions: [
    StarterKit.configure({ codeBlock: false }),
    DiagramCodeBlock,
    TableKit,
    TaskList,
    TaskItem.configure({ nested: true }),
    Image,
    Markdown,
  ],
})
const normalize = (markdown: string) => asLiveMarkdown(editor, markdown)

/** `original` opened in the editor, `from` changed to `to` there, and saved. */
function save(original: string, from: string, to: string): string {
  const shown = normalize(original)
  expect(shown).toContain(from)
  return preserveFormatting(original, shown.replace(from, to), normalize)
}

const table = "| Name | Role |\n|---|---|\n| Ada | Lead |"
const page = [
  "# Handbook",
  "",
  "<!-- Keep in sync with the wiki. -->",
  "",
  "Welcome to the [team][wiki].",
  "",
  "* Read this",
  "* Then that",
  "",
  table,
  "",
  "Questions go to the channel.",
  "",
  "[wiki]: https://example.com/wiki",
  "",
].join("\n")

describe("preserveFormatting", () => {
  it.each([
    ["* x\n* y", "- y", "- y\n- z", "- x\n- y\n- z"],
    ["1) x\n2) y", "2. y", "2. y\n3. z", "1. x\n2. y\n3. z"],
  ])("keeps an appended item in the same list", (list, from, to, saved) => {
    expect(save(`${list}\n\npara`, from, to)).toBe(`${saved}\n\npara`)
  })

  it("changes only the blocks that were edited", () => {
    expect(save(page, "the channel", "#help")).toBe(
      page.replace("the channel", "#help")
    )
  })

  it("replaces an edited table once, in place", () => {
    const edited = normalize(table).trim().replace("Lead", "Chair")
    expect(save(page, "Lead", "Chair")).toBe(page.replace(table, edited))
  })

  it("keeps added paragraphs where they were written", () => {
    expect(save(page, "- Then that", "- Then that\n\nSay hi.")).toBe(
      page.replace("* Then that", "* Then that\n\nSay hi.")
    )
  })

  it("keeps a CRLF page's line endings and everything nobody edited", () => {
    const crlf = page.replaceAll("\n", "\r\n")
    expect(save(crlf, "the channel", "#help")).toBe(
      crlf.replace("the channel", "#help")
    )
  })

  it("keeps each line's own ending when a page mixes them", () => {
    // Mostly CRLF, with a heading and a list someone wrote with LF.
    const mixed = page
      .replaceAll("\n", "\r\n")
      .replace("# Handbook\r\n", "# Handbook\n")
      .replace("* Read this\r\n* Then that\r\n", "* Read this\n* Then that\n")
    expect(save(mixed, "the channel", "#help")).toBe(
      mixed.replace("the channel", "#help")
    )
    // A line the editor adds takes the ending most lines have.
    expect(save(mixed, "- Then that", "- Then that\n\nSay hi.")).toBe(
      mixed.replace("* Then that\n\r\n", "* Then that\n\r\nSay hi.\r\n\r\n")
    )
  })
})
