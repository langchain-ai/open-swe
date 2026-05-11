import { stringWidth } from "./string-width.js";
import { wrapAnsi } from "./wrap-ansi.js";
import {
  firstGrapheme,
  getGraphemeSegmenter,
  getWordSegmenter,
} from "./intl.js";

/**
 * Kill ring for storing killed (cut) text that can be yanked (pasted) with Ctrl+Y.
 * Module-level state shares one kill ring across all input fields.
 *
 * Consecutive kills accumulate until the user types some other key.
 * Alt+Y cycles through previous kills after a yank.
 */
const KILL_RING_MAX_SIZE = 10;
let killRing: string[] = [];
let killRingIndex = 0;
let lastActionWasKill = false;

let lastYankStart = 0;
let lastYankLength = 0;
let lastActionWasYank = false;

export function pushToKillRing(
  text: string,
  direction: "prepend" | "append" = "append",
): void {
  if (text.length === 0) return;
  if (lastActionWasKill && killRing.length > 0) {
    if (direction === "prepend") {
      killRing[0] = text + killRing[0];
    } else {
      killRing[0] = killRing[0] + text;
    }
  } else {
    killRing.unshift(text);
    if (killRing.length > KILL_RING_MAX_SIZE) {
      killRing.pop();
    }
  }
  lastActionWasKill = true;
  lastActionWasYank = false;
}

export function getLastKill(): string {
  return killRing[0] ?? "";
}

export function clearKillRing(): void {
  killRing = [];
  killRingIndex = 0;
  lastActionWasKill = false;
  lastActionWasYank = false;
  lastYankStart = 0;
  lastYankLength = 0;
}

export function resetKillAccumulation(): void {
  lastActionWasKill = false;
}

export function recordYank(start: number, length: number): void {
  lastYankStart = start;
  lastYankLength = length;
  lastActionWasYank = true;
  killRingIndex = 0;
}

export function yankPop(): {
  text: string;
  start: number;
  length: number;
} | null {
  if (!lastActionWasYank || killRing.length <= 1) return null;
  killRingIndex = (killRingIndex + 1) % killRing.length;
  const text = killRing[killRingIndex] ?? "";
  return { text, start: lastYankStart, length: lastYankLength };
}

export function updateYankLength(length: number): void {
  lastYankLength = length;
}

export function resetYankState(): void {
  lastActionWasYank = false;
}

const WHITESPACE_REGEX = /\s/;

type Position = {
  line: number;
  column: number;
};

export class Cursor {
  readonly offset: number;
  constructor(
    readonly measuredText: MeasuredText,
    offset: number = 0,
    readonly selection: number = 0,
  ) {
    this.offset = Math.max(0, Math.min(this.text.length, offset));
  }

  static fromText(
    text: string,
    columns: number,
    offset: number = 0,
    selection: number = 0,
  ): Cursor {
    return new Cursor(new MeasuredText(text, columns - 1), offset, selection);
  }

  getViewportStartLine(maxVisibleLines?: number): number {
    if (maxVisibleLines === undefined || maxVisibleLines <= 0) return 0;
    const { line } = this.getPosition();
    const allLines = this.measuredText.getWrappedText();
    if (allLines.length <= maxVisibleLines) return 0;
    const half = Math.floor(maxVisibleLines / 2);
    let startLine = Math.max(0, line - half);
    const endLine = Math.min(allLines.length, startLine + maxVisibleLines);
    if (endLine - startLine < maxVisibleLines) {
      startLine = Math.max(0, endLine - maxVisibleLines);
    }
    return startLine;
  }

  getViewportCharOffset(maxVisibleLines?: number): number {
    const startLine = this.getViewportStartLine(maxVisibleLines);
    if (startLine === 0) return 0;
    const wrappedLines = this.measuredText.getWrappedLines();
    return wrappedLines[startLine]?.startOffset ?? 0;
  }

  getViewportCharEnd(maxVisibleLines?: number): number {
    const startLine = this.getViewportStartLine(maxVisibleLines);
    const allLines = this.measuredText.getWrappedLines();
    if (maxVisibleLines === undefined || maxVisibleLines <= 0)
      return this.text.length;
    const endLine = Math.min(allLines.length, startLine + maxVisibleLines);
    if (endLine >= allLines.length) return this.text.length;
    return allLines[endLine]?.startOffset ?? this.text.length;
  }

  render(
    cursorChar: string,
    mask: string,
    invert: (text: string) => string,
    ghostText?: { text: string; dim: (text: string) => string },
    maxVisibleLines?: number,
  ): string {
    const { line, column } = this.getPosition();
    const allLines = this.measuredText.getWrappedText();

    const startLine = this.getViewportStartLine(maxVisibleLines);
    const endLine =
      maxVisibleLines !== undefined && maxVisibleLines > 0
        ? Math.min(allLines.length, startLine + maxVisibleLines)
        : allLines.length;

    return allLines
      .slice(startLine, endLine)
      .map((text, i) => {
        const currentLine = i + startLine;
        let displayText = text;
        if (mask) {
          const graphemes = Array.from(getGraphemeSegmenter().segment(text));
          if (currentLine === allLines.length - 1) {
            const visibleCount = Math.min(6, graphemes.length);
            const maskCount = graphemes.length - visibleCount;
            const splitOffset =
              graphemes.length > visibleCount ? graphemes[maskCount]!.index : 0;
            displayText = mask.repeat(maskCount) + text.slice(splitOffset);
          } else {
            displayText = mask.repeat(graphemes.length);
          }
        }
        if (line !== currentLine) return displayText.trimEnd();

        let beforeCursor = "";
        let atCursor = cursorChar;
        let afterCursor = "";
        let currentWidth = 0;
        let cursorFound = false;

        for (const { segment } of getGraphemeSegmenter().segment(displayText)) {
          if (cursorFound) {
            afterCursor += segment;
            continue;
          }
          const nextWidth = currentWidth + stringWidth(segment);
          if (nextWidth > column) {
            atCursor = segment;
            cursorFound = true;
          } else {
            currentWidth = nextWidth;
            beforeCursor += segment;
          }
        }

        let renderedCursor: string;
        let ghostSuffix = "";
        if (
          ghostText &&
          currentLine === allLines.length - 1 &&
          this.isAtEnd() &&
          ghostText.text.length > 0
        ) {
          const firstGhostChar =
            firstGrapheme(ghostText.text) || ghostText.text[0]!;
          renderedCursor = cursorChar ? invert(firstGhostChar) : firstGhostChar;
          const ghostRest = ghostText.text.slice(firstGhostChar.length);
          if (ghostRest.length > 0) {
            ghostSuffix = ghostText.dim(ghostRest);
          }
        } else {
          renderedCursor = cursorChar ? invert(atCursor) : atCursor;
        }

        return (
          beforeCursor + renderedCursor + ghostSuffix + afterCursor.trimEnd()
        );
      })
      .join("\n");
  }

  left(): Cursor {
    if (this.offset === 0) return this;
    const chip = this.imageRefEndingAt(this.offset);
    if (chip) return new Cursor(this.measuredText, chip.start);
    const prevOffset = this.measuredText.prevOffset(this.offset);
    return new Cursor(this.measuredText, prevOffset);
  }

  right(): Cursor {
    if (this.offset >= this.text.length) return this;
    const chip = this.imageRefStartingAt(this.offset);
    if (chip) return new Cursor(this.measuredText, chip.end);
    const nextOffset = this.measuredText.nextOffset(this.offset);
    return new Cursor(
      this.measuredText,
      Math.min(nextOffset, this.text.length),
    );
  }

  /**
   * If an [Image #N] chip ends at `offset`, return its bounds. Used by left()
   * to hop the cursor over the chip instead of stepping into it.
   */
  imageRefEndingAt(offset: number): { start: number; end: number } | null {
    const m = this.text.slice(0, offset).match(/\[Image #\d+\]$/);
    return m ? { start: offset - m[0].length, end: offset } : null;
  }

  imageRefStartingAt(offset: number): { start: number; end: number } | null {
    const m = this.text.slice(offset).match(/^\[Image #\d+\]/);
    return m ? { start: offset, end: offset + m[0].length } : null;
  }

  /**
   * If offset lands strictly inside an [Image #N] chip, snap it to the given
   * boundary. Used by word-movement methods so Ctrl+W / Alt+D never leave a
   * partial chip.
   */
  snapOutOfImageRef(offset: number, toward: "start" | "end"): number {
    const re = /\[Image #\d+\]/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(this.text)) !== null) {
      const start = m.index;
      const end = start + m[0].length;
      if (offset > start && offset < end) {
        return toward === "start" ? start : end;
      }
    }
    return offset;
  }

  up(): Cursor {
    const { line, column } = this.getPosition();
    if (line === 0) return this;
    const prevLine = this.measuredText.getWrappedText()[line - 1];
    if (prevLine === undefined) return this;
    const prevLineDisplayWidth = stringWidth(prevLine);
    if (column > prevLineDisplayWidth) {
      const newOffset = this.getOffset({
        line: line - 1,
        column: prevLineDisplayWidth,
      });
      return new Cursor(this.measuredText, newOffset, 0);
    }
    const newOffset = this.getOffset({ line: line - 1, column });
    return new Cursor(this.measuredText, newOffset, 0);
  }

  down(): Cursor {
    const { line, column } = this.getPosition();
    if (line >= this.measuredText.lineCount - 1) return this;
    const nextLine = this.measuredText.getWrappedText()[line + 1];
    if (nextLine === undefined) return this;
    const nextLineDisplayWidth = stringWidth(nextLine);
    if (column > nextLineDisplayWidth) {
      const newOffset = this.getOffset({
        line: line + 1,
        column: nextLineDisplayWidth,
      });
      return new Cursor(this.measuredText, newOffset, 0);
    }
    const newOffset = this.getOffset({ line: line + 1, column });
    return new Cursor(this.measuredText, newOffset, 0);
  }

  private startOfCurrentLine(): Cursor {
    const { line } = this.getPosition();
    return new Cursor(
      this.measuredText,
      this.getOffset({ line, column: 0 }),
      0,
    );
  }

  startOfLine(): Cursor {
    const { line, column } = this.getPosition();
    if (column === 0 && line > 0) {
      return new Cursor(
        this.measuredText,
        this.getOffset({ line: line - 1, column: 0 }),
        0,
      );
    }
    return this.startOfCurrentLine();
  }

  endOfLine(): Cursor {
    const { line } = this.getPosition();
    const column = this.measuredText.getLineLength(line);
    const offset = this.getOffset({ line, column });
    return new Cursor(this.measuredText, offset, 0);
  }

  private findLogicalLineStart(fromOffset: number = this.offset): number {
    const prevNewline = this.text.lastIndexOf("\n", fromOffset - 1);
    return prevNewline === -1 ? 0 : prevNewline + 1;
  }

  private findLogicalLineEnd(fromOffset: number = this.offset): number {
    const nextNewline = this.text.indexOf("\n", fromOffset);
    return nextNewline === -1 ? this.text.length : nextNewline;
  }

  private getLogicalLineBounds(): { start: number; end: number } {
    return {
      start: this.findLogicalLineStart(),
      end: this.findLogicalLineEnd(),
    };
  }

  private createCursorWithColumn(
    lineStart: number,
    lineEnd: number,
    targetColumn: number,
  ): Cursor {
    const lineLength = lineEnd - lineStart;
    const clampedColumn = Math.min(targetColumn, lineLength);
    const rawOffset = lineStart + clampedColumn;
    const offset = this.measuredText.snapToGraphemeBoundary(rawOffset);
    return new Cursor(this.measuredText, offset, 0);
  }

  upLogicalLine(): Cursor {
    const { start: currentStart } = this.getLogicalLineBounds();
    if (currentStart === 0) return new Cursor(this.measuredText, 0, 0);
    const currentColumn = this.offset - currentStart;
    const prevLineEnd = currentStart - 1;
    const prevLineStart = this.findLogicalLineStart(prevLineEnd);
    return this.createCursorWithColumn(
      prevLineStart,
      prevLineEnd,
      currentColumn,
    );
  }

  downLogicalLine(): Cursor {
    const { start: currentStart, end: currentEnd } =
      this.getLogicalLineBounds();
    if (currentEnd >= this.text.length) {
      return new Cursor(this.measuredText, this.text.length, 0);
    }
    const currentColumn = this.offset - currentStart;
    const nextLineStart = currentEnd + 1;
    const nextLineEnd = this.findLogicalLineEnd(nextLineStart);
    return this.createCursorWithColumn(
      nextLineStart,
      nextLineEnd,
      currentColumn,
    );
  }

  nextWord(): Cursor {
    if (this.isAtEnd()) return this;
    const wordBoundaries = this.measuredText.getWordBoundaries();
    for (const boundary of wordBoundaries) {
      if (boundary.isWordLike && boundary.start > this.offset) {
        return new Cursor(this.measuredText, boundary.start);
      }
    }
    return new Cursor(this.measuredText, this.text.length);
  }

  prevWord(): Cursor {
    if (this.isAtStart()) return this;
    const wordBoundaries = this.measuredText.getWordBoundaries();
    let prevWordStart: number | null = null;
    for (const boundary of wordBoundaries) {
      if (!boundary.isWordLike) continue;
      if (boundary.start < this.offset) {
        if (this.offset > boundary.start && this.offset <= boundary.end) {
          return new Cursor(this.measuredText, boundary.start);
        }
        prevWordStart = boundary.start;
      }
    }
    if (prevWordStart !== null)
      return new Cursor(this.measuredText, prevWordStart);
    return new Cursor(this.measuredText, 0);
  }

  modifyText(end: Cursor, insertString: string = ""): Cursor {
    const startOffset = this.offset;
    const endOffset = end.offset;
    const newText =
      this.text.slice(0, startOffset) +
      insertString +
      this.text.slice(endOffset);
    return Cursor.fromText(
      newText,
      this.columns,
      startOffset + insertString.normalize("NFC").length,
    );
  }

  insert(insertString: string): Cursor {
    return this.modifyText(this, insertString);
  }

  del(): Cursor {
    if (this.isAtEnd()) return this;
    return this.modifyText(this.right());
  }

  backspace(): Cursor {
    if (this.isAtStart()) return this;
    return this.left().modifyText(this);
  }

  deleteToLineStart(): { cursor: Cursor; killed: string } {
    if (this.offset > 0 && this.text[this.offset - 1] === "\n") {
      return { cursor: this.left().modifyText(this), killed: "\n" };
    }
    const startCursor = this.startOfLine();
    const killed = this.text.slice(startCursor.offset, this.offset);
    return { cursor: startCursor.modifyText(this), killed };
  }

  deleteToLineEnd(): { cursor: Cursor; killed: string } {
    if (this.text[this.offset] === "\n") {
      return { cursor: this.modifyText(this.right()), killed: "\n" };
    }
    const endCursor = this.endOfLine();
    const killed = this.text.slice(this.offset, endCursor.offset);
    return { cursor: this.modifyText(endCursor), killed };
  }

  deleteWordBefore(): { cursor: Cursor; killed: string } {
    if (this.isAtStart()) return { cursor: this, killed: "" };
    const target = this.snapOutOfImageRef(this.prevWord().offset, "start");
    const prevWordCursor = new Cursor(this.measuredText, target);
    const killed = this.text.slice(prevWordCursor.offset, this.offset);
    return { cursor: prevWordCursor.modifyText(this), killed };
  }

  /**
   * Deletes a token before the cursor if one exists.
   * Supports refs: [Pasted text #N], [Image #N], [...Truncated text #N +N lines...].
   *
   * Note: @mentions are NOT tokenized since users may want to correct typos
   * in file paths. Use Ctrl/Cmd+backspace for word-deletion on mentions.
   *
   * Returns null if no token found at cursor position.
   * Only triggers when cursor is at end of token (followed by whitespace or EOL).
   */
  deleteTokenBefore(): Cursor | null {
    const chipAfter = this.imageRefStartingAt(this.offset);
    if (chipAfter) {
      const end =
        this.text[chipAfter.end] === " " ? chipAfter.end + 1 : chipAfter.end;
      return this.modifyText(new Cursor(this.measuredText, end));
    }

    if (this.isAtStart()) return null;

    const charAfter = this.text[this.offset];
    if (charAfter !== undefined && !/\s/.test(charAfter)) return null;

    const textBefore = this.text.slice(0, this.offset);
    const pasteMatch = textBefore.match(
      /(^|\s)\[(Pasted text #\d+(?: \+\d+ lines)?|Image #\d+|\.\.\.Truncated text #\d+ \+\d+ lines\.\.\.)\]$/,
    );
    if (pasteMatch) {
      const matchStart = pasteMatch.index! + pasteMatch[1]!.length;
      return new Cursor(this.measuredText, matchStart).modifyText(this);
    }
    return null;
  }

  deleteWordAfter(): Cursor {
    if (this.isAtEnd()) return this;
    const target = this.snapOutOfImageRef(this.nextWord().offset, "end");
    return this.modifyText(new Cursor(this.measuredText, target));
  }

  equals(other: Cursor): boolean {
    return (
      this.offset === other.offset && this.measuredText === other.measuredText
    );
  }

  isAtStart(): boolean {
    return this.offset === 0;
  }

  isAtEnd(): boolean {
    return this.offset >= this.text.length;
  }

  public get text(): string {
    return this.measuredText.text;
  }

  private get columns(): number {
    return this.measuredText.columns + 1;
  }

  getPosition(): Position {
    return this.measuredText.getPositionFromOffset(this.offset);
  }

  private getOffset(position: Position): number {
    return this.measuredText.getOffsetFromPosition(position);
  }
}

class WrappedLine {
  constructor(
    public readonly text: string,
    public readonly startOffset: number,
    public readonly isPrecededByNewline: boolean,
    public readonly endsWithNewline: boolean = false,
  ) {}

  get length(): number {
    return this.text.length + (this.endsWithNewline ? 1 : 0);
  }
}

export class MeasuredText {
  private _wrappedLines?: WrappedLine[];
  public readonly text: string;
  private navigationCache: Map<string, number>;
  private graphemeBoundaries?: number[];
  private wordBoundariesCache?: Array<{
    start: number;
    end: number;
    isWordLike: boolean;
  }>;

  constructor(
    text: string,
    readonly columns: number,
  ) {
    this.text = text.normalize("NFC");
    this.navigationCache = new Map();
  }

  private get wrappedLines(): WrappedLine[] {
    if (!this._wrappedLines) {
      this._wrappedLines = this.measureWrappedText();
    }
    return this._wrappedLines;
  }

  private getGraphemeBoundaries(): number[] {
    if (!this.graphemeBoundaries) {
      this.graphemeBoundaries = [];
      for (const { index } of getGraphemeSegmenter().segment(this.text)) {
        this.graphemeBoundaries.push(index);
      }
      this.graphemeBoundaries.push(this.text.length);
    }
    return this.graphemeBoundaries;
  }

  /**
   * Get word boundaries using Intl.Segmenter for proper Unicode word segmentation.
   * This correctly handles CJK (Chinese, Japanese, Korean) text where each character
   * is typically its own word, as well as scripts that use spaces between words.
   */
  public getWordBoundaries(): Array<{
    start: number;
    end: number;
    isWordLike: boolean;
  }> {
    if (!this.wordBoundariesCache) {
      this.wordBoundariesCache = [];
      for (const segment of getWordSegmenter().segment(this.text)) {
        this.wordBoundariesCache.push({
          start: segment.index,
          end: segment.index + segment.segment.length,
          isWordLike: segment.isWordLike ?? false,
        });
      }
    }
    return this.wordBoundariesCache;
  }

  private binarySearchBoundary(
    boundaries: number[],
    target: number,
    findNext: boolean,
  ): number {
    let left = 0;
    let right = boundaries.length - 1;
    let result = findNext ? this.text.length : 0;

    while (left <= right) {
      const mid = Math.floor((left + right) / 2);
      const boundary = boundaries[mid];
      if (boundary === undefined) break;
      if (findNext) {
        if (boundary > target) {
          result = boundary;
          right = mid - 1;
        } else {
          left = mid + 1;
        }
      } else {
        if (boundary < target) {
          result = boundary;
          left = mid + 1;
        } else {
          right = mid - 1;
        }
      }
    }
    return result;
  }

  public stringIndexToDisplayWidth(text: string, index: number): number {
    if (index <= 0) return 0;
    if (index >= text.length) return stringWidth(text);
    return stringWidth(text.substring(0, index));
  }

  public displayWidthToStringIndex(text: string, targetWidth: number): number {
    if (targetWidth <= 0) return 0;
    if (!text) return 0;

    if (text === this.text) {
      return this.offsetAtDisplayWidth(targetWidth);
    }

    let currentWidth = 0;
    let currentOffset = 0;
    for (const { segment, index } of getGraphemeSegmenter().segment(text)) {
      const segmentWidth = stringWidth(segment);
      if (currentWidth + segmentWidth > targetWidth) break;
      currentWidth += segmentWidth;
      currentOffset = index + segment.length;
    }
    return currentOffset;
  }

  private offsetAtDisplayWidth(targetWidth: number): number {
    if (targetWidth <= 0) return 0;
    let currentWidth = 0;
    const boundaries = this.getGraphemeBoundaries();
    for (let i = 0; i < boundaries.length - 1; i++) {
      const start = boundaries[i];
      const end = boundaries[i + 1];
      if (start === undefined || end === undefined) continue;
      const segment = this.text.substring(start, end);
      const segmentWidth = stringWidth(segment);
      if (currentWidth + segmentWidth > targetWidth) return start;
      currentWidth += segmentWidth;
    }
    return this.text.length;
  }

  private measureWrappedText(): WrappedLine[] {
    const wrappedText = wrapAnsi(this.text, this.columns, {
      hard: true,
      trim: false,
    });
    const wrappedLines: WrappedLine[] = [];
    let searchOffset = 0;
    let lastNewLinePos = -1;

    const lines = wrappedText.split("\n");
    for (let i = 0; i < lines.length; i++) {
      const text = lines[i]!;
      const isPrecededByNewline = (startOffset: number): boolean =>
        i === 0 || (startOffset > 0 && this.text[startOffset - 1] === "\n");

      if (text.length === 0) {
        lastNewLinePos = this.text.indexOf("\n", lastNewLinePos + 1);
        if (lastNewLinePos !== -1) {
          const startOffset = lastNewLinePos;
          wrappedLines.push(
            new WrappedLine(
              text,
              startOffset,
              isPrecededByNewline(startOffset),
              true,
            ),
          );
        } else {
          const startOffset = this.text.length;
          wrappedLines.push(
            new WrappedLine(
              text,
              startOffset,
              isPrecededByNewline(startOffset),
              false,
            ),
          );
        }
      } else {
        const startOffset = this.text.indexOf(text, searchOffset);
        if (startOffset === -1) {
          throw new Error("Failed to find wrapped line in text");
        }
        searchOffset = startOffset + text.length;
        const potentialNewlinePos = startOffset + text.length;
        const endsWithNewline =
          potentialNewlinePos < this.text.length &&
          this.text[potentialNewlinePos] === "\n";
        if (endsWithNewline) lastNewLinePos = potentialNewlinePos;
        wrappedLines.push(
          new WrappedLine(
            text,
            startOffset,
            isPrecededByNewline(startOffset),
            endsWithNewline,
          ),
        );
      }
    }
    return wrappedLines;
  }

  public getWrappedText(): string[] {
    return this.wrappedLines.map((line) =>
      line.isPrecededByNewline ? line.text : line.text.trimStart(),
    );
  }

  public getWrappedLines(): WrappedLine[] {
    return this.wrappedLines;
  }

  private getLine(line: number): WrappedLine {
    const lines = this.wrappedLines;
    return lines[Math.max(0, Math.min(line, lines.length - 1))]!;
  }

  public getOffsetFromPosition(position: Position): number {
    const wrappedLine = this.getLine(position.line);
    if (wrappedLine.text.length === 0 && wrappedLine.endsWithNewline) {
      return wrappedLine.startOffset;
    }
    const leadingWhitespace = wrappedLine.isPrecededByNewline
      ? 0
      : wrappedLine.text.length - wrappedLine.text.trimStart().length;
    const displayColumnWithLeading = position.column + leadingWhitespace;
    const stringIndex = this.displayWidthToStringIndex(
      wrappedLine.text,
      displayColumnWithLeading,
    );
    const offset = wrappedLine.startOffset + stringIndex;
    const lineEnd = wrappedLine.startOffset + wrappedLine.text.length;
    let maxOffset = lineEnd;
    const lineDisplayWidth = stringWidth(wrappedLine.text);
    if (wrappedLine.endsWithNewline && position.column > lineDisplayWidth) {
      maxOffset = lineEnd + 1;
    }
    return Math.min(offset, maxOffset);
  }

  public getLineLength(line: number): number {
    const wrappedLine = this.getLine(line);
    return stringWidth(wrappedLine.text);
  }

  public getPositionFromOffset(offset: number): Position {
    const lines = this.wrappedLines;
    for (let line = 0; line < lines.length; line++) {
      const currentLine = lines[line]!;
      const nextLine = lines[line + 1];
      if (
        offset >= currentLine.startOffset &&
        (!nextLine || offset < nextLine.startOffset)
      ) {
        const stringPosInLine = offset - currentLine.startOffset;
        let displayColumn: number;
        if (currentLine.isPrecededByNewline) {
          displayColumn = this.stringIndexToDisplayWidth(
            currentLine.text,
            stringPosInLine,
          );
        } else {
          const leadingWhitespace =
            currentLine.text.length - currentLine.text.trimStart().length;
          if (stringPosInLine < leadingWhitespace) {
            displayColumn = 0;
          } else {
            const trimmedText = currentLine.text.trimStart();
            const posInTrimmed = stringPosInLine - leadingWhitespace;
            displayColumn = this.stringIndexToDisplayWidth(
              trimmedText,
              posInTrimmed,
            );
          }
        }
        return { line, column: Math.max(0, displayColumn) };
      }
    }
    const line = lines.length - 1;
    const lastLine = this.wrappedLines[line]!;
    return { line, column: stringWidth(lastLine.text) };
  }

  public get lineCount(): number {
    return this.wrappedLines.length;
  }

  private withCache<T>(key: string, compute: () => T): T {
    const cached = this.navigationCache.get(key);
    if (cached !== undefined) return cached as T;
    const result = compute();
    this.navigationCache.set(key, result as number);
    return result;
  }

  nextOffset(offset: number): number {
    return this.withCache(`next:${offset}`, () => {
      const boundaries = this.getGraphemeBoundaries();
      return this.binarySearchBoundary(boundaries, offset, true);
    });
  }

  prevOffset(offset: number): number {
    if (offset <= 0) return 0;
    return this.withCache(`prev:${offset}`, () => {
      const boundaries = this.getGraphemeBoundaries();
      return this.binarySearchBoundary(boundaries, offset, false);
    });
  }

  /**
   * Snap an arbitrary code-unit offset to the start of the containing grapheme.
   * If offset is already on a boundary, returns it unchanged.
   */
  snapToGraphemeBoundary(offset: number): number {
    if (offset <= 0) return 0;
    if (offset >= this.text.length) return this.text.length;
    const boundaries = this.getGraphemeBoundaries();
    let lo = 0;
    let hi = boundaries.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (boundaries[mid]! <= offset) lo = mid;
      else hi = mid - 1;
    }
    return boundaries[lo]!;
  }
}

// Suppress unused warnings for symbols only used by the kill-ring API surface
// for callers that import them directly (e.g. tests).
void WHITESPACE_REGEX;
