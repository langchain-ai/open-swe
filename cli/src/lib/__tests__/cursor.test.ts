import { describe, it, expect } from "vitest";
import { Cursor } from "../text-input/cursor.js";

describe("Cursor.fromText", () => {
  it("places the cursor at the requested offset", () => {
    const c = Cursor.fromText("hello world", 80, 6);
    expect(c.offset).toBe(6);
    expect(c.text).toBe("hello world");
  });

  it("clamps offset within the text bounds", () => {
    const c = Cursor.fromText("hi", 80, 99);
    expect(c.offset).toBe(2);
  });
});

describe("basic cursor movement", () => {
  it("moves left by one grapheme", () => {
    const c = Cursor.fromText("abc", 80, 2).left();
    expect(c.offset).toBe(1);
  });

  it("moves right by one grapheme", () => {
    const c = Cursor.fromText("abc", 80, 1).right();
    expect(c.offset).toBe(2);
  });

  it("does not go past the end", () => {
    const c = Cursor.fromText("abc", 80, 3).right();
    expect(c.offset).toBe(3);
  });

  it("does not go before the start", () => {
    const c = Cursor.fromText("abc", 80, 0).left();
    expect(c.offset).toBe(0);
  });

  it("jumps over [Image #N] chips when moving left", () => {
    const c = Cursor.fromText("hi [Image #1] there", 80, 13).left();
    // 13 is just past `[Image #1]` (positions 3..13). Left should land at 3.
    expect(c.offset).toBe(3);
  });

  it("jumps over [Image #N] chips when moving right", () => {
    const c = Cursor.fromText("hi [Image #1] there", 80, 3).right();
    expect(c.offset).toBe(13);
  });
});

describe("word movement", () => {
  it("jumps to next word", () => {
    const c = Cursor.fromText("foo bar baz", 80, 0).nextWord();
    expect(c.offset).toBeGreaterThan(0);
    expect(c.text.slice(c.offset, c.offset + 3)).toBe("bar");
  });

  it("jumps to previous word", () => {
    const c = Cursor.fromText("foo bar baz", 80, 8).prevWord();
    expect(c.text.slice(c.offset, c.offset + 3)).toBe("bar");
  });
});

describe("text editing", () => {
  it("inserts text at the cursor", () => {
    const c = Cursor.fromText("helo", 80, 3).insert("l");
    expect(c.text).toBe("hello");
    expect(c.offset).toBe(4);
  });

  it("backspace deletes the previous grapheme", () => {
    const c = Cursor.fromText("hello", 80, 5).backspace();
    expect(c.text).toBe("hell");
    expect(c.offset).toBe(4);
  });

  it("del removes the next grapheme", () => {
    const c = Cursor.fromText("hello", 80, 0).del();
    expect(c.text).toBe("ello");
    expect(c.offset).toBe(0);
  });

  it("deleteWordBefore removes the word to the left", () => {
    const { cursor } = Cursor.fromText("foo bar", 80, 7).deleteWordBefore();
    expect(cursor.text).toBe("foo ");
  });

  it("deleteToLineEnd removes from cursor to end of line", () => {
    const { cursor, killed } = Cursor.fromText(
      "hello world",
      80,
      5,
    ).deleteToLineEnd();
    expect(cursor.text).toBe("hello");
    expect(killed).toBe(" world");
  });

  it("deleteToLineStart removes from cursor to start of line", () => {
    const { cursor, killed } = Cursor.fromText(
      "hello world",
      80,
      5,
    ).deleteToLineStart();
    expect(cursor.text).toBe(" world");
    expect(killed).toBe("hello");
  });

  it("deleteTokenBefore removes a complete [Image #N] ref", () => {
    // Cursor sits right after the `]`, before the trailing space — the same
    // position you'd be in after dropping a pill and pressing backspace. The
    // pill goes away and both the space before and after stay, matching the
    // reference behavior (the chip-start branch handles the "select the pill"
    // case where the trailing space is also consumed).
    const c = Cursor.fromText("hi [Image #1] ", 80, 13);
    const next = c.deleteTokenBefore();
    expect(next).not.toBe(null);
    expect(next?.text).toBe("hi  ");
  });

  it("deleteTokenBefore at the chip start consumes the chip and trailing space", () => {
    // Cursor sits AT `[`, simulating the "pill is selected" state.
    const c = Cursor.fromText("hi [Image #1] more", 80, 3);
    const next = c.deleteTokenBefore();
    expect(next).not.toBe(null);
    expect(next?.text).toBe("hi more");
  });
});

describe("rendering", () => {
  it("renders a cursor on empty input", () => {
    const c = Cursor.fromText("", 80, 0);
    const rendered = c.render(" ", "", (s) => `[${s}]`);
    expect(rendered).toContain("[");
  });

  it("renders text with cursor highlighting", () => {
    const c = Cursor.fromText("abc", 80, 1);
    const rendered = c.render(" ", "", (s) => `<${s}>`);
    expect(rendered).toContain("<b>");
  });
});

describe("multiline", () => {
  it("reports the correct line for a position past a newline", () => {
    const c = Cursor.fromText("line1\nline2", 80, 7);
    const pos = c.getPosition();
    expect(pos.line).toBe(1);
    expect(pos.column).toBe(1);
  });

  it("can move down a logical line", () => {
    const c = Cursor.fromText("line1\nline2", 80, 1).downLogicalLine();
    expect(c.offset).toBe(7);
  });

  it("can move up a logical line", () => {
    const c = Cursor.fromText("line1\nline2", 80, 7).upLogicalLine();
    expect(c.offset).toBe(1);
  });
});
