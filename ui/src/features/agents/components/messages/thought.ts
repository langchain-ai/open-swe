const MAX_LABEL_LENGTH = 200

const HEADING_LINE = /^\s*(?:#{1,6}\s+(.+?)|\*\*([^*]+)\*\*|__([^_]+)__)\s*$/

/** A model's reasoning, read for the labels that stand in for it in the transcript. */
export class Thought {
  readonly text: string
  private readonly lines: Array<string>

  constructor(text: string) {
    this.text = text.trim()
    this.lines = text.split("\n")
  }

  /** The thought's first line as plain text; the row's own truncation handles overflow. */
  get preview(): string {
    const firstLine = this.text.split("\n").find((line) => line.trim()) ?? ""
    return Thought.plain(firstLine.replace(/^\s*#+\s*/, ""))
  }

  /** The latest heading the model gave its thought, once that line has fully streamed. */
  get heading(): string | null {
    const heading = this.lines
      .slice(0, -1)
      .findLast((line) => HEADING_LINE.test(line))
    return heading ? Thought.plain(Thought.headingText(heading)) : null
  }

  /** The thought without its leading heading when the row above already shows that heading. */
  bodyUnder(shown: string | null): string {
    const [firstLine = "", ...rest] = this.text.split("\n")
    const leading = HEADING_LINE.test(firstLine)
      ? Thought.plain(Thought.headingText(firstLine))
      : null
    return leading !== null && leading === shown
      ? rest.join("\n").trim()
      : this.text
  }

  private static headingText(line: string): string {
    const match = HEADING_LINE.exec(line)
    return match?.[1] ?? match?.[2] ?? match?.[3] ?? line
  }

  /** Markdown markers stripped; underscores are emphasis only at a word edge, inside a name they are the name. */
  private static plain(line: string): string {
    return line
      .replace(/[*`]/g, "")
      .replace(/(^|\W)_+|_+(?=\W|$)/g, "$1")
      .trim()
      .slice(0, MAX_LABEL_LENGTH)
  }
}
