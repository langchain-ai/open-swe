/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest"

import {
  MARKDOWN_TOOLBAR,
  applyMarkdownAction,
  prefixLines,
  wrapSelection,
} from "./markdownEditing"

describe("wrapSelection", () => {
  it("wraps the selected text and reselects it", () => {
    expect(
      wrapSelection({ value: "hello world", start: 6, end: 11 }, "**", "bold")
    ).toEqual({ value: "hello **world**", start: 8, end: 13 })
  })

  it("inserts the placeholder when nothing is selected", () => {
    expect(
      wrapSelection({ value: "", start: 0, end: 0 }, "_", "italic text")
    ).toEqual({ value: "_italic text_", start: 1, end: 12 })
  })
})

describe("prefixLines", () => {
  it("numbers every line the selection touches", () => {
    expect(
      prefixLines(
        { value: "a\nb\nc", start: 0, end: 5 },
        (index) => `${index + 1}. `
      )
    ).toEqual({ value: "1. a\n2. b\n3. c", start: 0, end: 14 })
  })

  it("prefixes from the start of the line the selection begins on", () => {
    expect(
      prefixLines({ value: "one\ntwo", start: 5, end: 7 }, () => "> ")
    ).toEqual({ value: "one\n> two", start: 4, end: 9 })
  })
})

describe("applyMarkdownAction", () => {
  const state = { value: "see docs", start: 4, end: 8 }

  it("bolds the selection", () => {
    expect(applyMarkdownAction(state, "bold")).toEqual({
      value: "see **docs**",
      start: 6,
      end: 10,
    })
  })

  it("italicises the selection", () => {
    expect(applyMarkdownAction(state, "italic")).toEqual({
      value: "see _docs_",
      start: 5,
      end: 9,
    })
  })

  it("wraps the selection in backticks", () => {
    expect(applyMarkdownAction(state, "code")).toEqual({
      value: "see `docs`",
      start: 5,
      end: 9,
    })
  })

  it("adds a heading prefix", () => {
    expect(applyMarkdownAction(state, "heading")).toEqual({
      value: "### see docs",
      start: 0,
      end: 12,
    })
  })

  it("adds a quote prefix", () => {
    expect(applyMarkdownAction(state, "quote")).toEqual({
      value: "> see docs",
      start: 0,
      end: 10,
    })
  })

  it("adds a bullet prefix", () => {
    expect(applyMarkdownAction(state, "ul")).toEqual({
      value: "- see docs",
      start: 0,
      end: 10,
    })
  })

  it("adds a task prefix", () => {
    expect(applyMarkdownAction(state, "task")).toEqual({
      value: "- [ ] see docs",
      start: 0,
      end: 14,
    })
  })

  it("numbers a multi-line ordered list", () => {
    expect(
      applyMarkdownAction({ value: "one\ntwo", start: 0, end: 7 }, "ol")
    ).toEqual({ value: "1. one\n2. two", start: 0, end: 13 })
  })

  it("turns the selection into a link and selects the url placeholder", () => {
    expect(applyMarkdownAction(state, "link")).toEqual({
      value: "see [docs](url)",
      start: 11,
      end: 14,
    })
  })

  it("uses a text placeholder for an empty link selection", () => {
    expect(
      applyMarkdownAction({ value: "", start: 0, end: 0 }, "link")
    ).toEqual({ value: "[text](url)", start: 7, end: 10 })
  })
})

describe("MARKDOWN_TOOLBAR", () => {
  it("groups the format actions ahead of the list actions", () => {
    expect(MARKDOWN_TOOLBAR.map((group) => group.map((i) => i.action))).toEqual(
      [
        ["heading", "bold", "italic", "quote", "code", "link"],
        ["ul", "ol", "task"],
      ]
    )
  })

  it("labels every action", () => {
    expect(MARKDOWN_TOOLBAR.flat().map((item) => item.label)).toEqual([
      "Heading",
      "Bold",
      "Italic",
      "Quote",
      "Code",
      "Link",
      "Bulleted list",
      "Numbered list",
      "Task list",
    ])
  })
})
