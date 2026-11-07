const MAX_LABEL_LENGTH = 200
/** Comfortably more than three lines of preview, which is all a passage shows. */
const MAX_PASSAGE_LENGTH = 600

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

  /**
   * What the model is on now: its latest heading, and the last paragraph it
   * wrote under that heading as one plain-text run, so a short preview follows
   * the thought as it streams.
   */
  get passage(): { heading: string | null; body: string } {
    const heading = this.heading
    const start = heading
      ? this.lines
          .slice(0, -1)
          .findLastIndex((line) => HEADING_LINE.test(line)) + 1
      : 0
    const paragraphs = this.lines
      .slice(start)
      .join("\n")
      .split(/\n\s*\n/)
      .filter((paragraph) => paragraph.trim())
    const body = (paragraphs.at(-1) ?? "")
      .split("\n")
      .map((line) => line.replace(/^\s*(?:#+|[-*]|\d+\.)\s+/, ""))
      .join(" ")
    return { heading, body: Thought.plain(body, MAX_PASSAGE_LENGTH) }
  }

  /** The latest heading the model gave its thought, once that line has fully streamed. */
  get heading(): string | null {
    const heading = this.lines
      .slice(0, -1)
      .findLast((line) => HEADING_LINE.test(line))
    return heading ? Thought.plain(Thought.headingText(heading)) : null
  }

  /** The live header: the latest heading, else the first line while no heading has streamed. */
  get liveLabel(): string {
    return this.heading || this.preview
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

  /** The live body, without the first line when the header is already showing it. */
  get liveBody(): string {
    if (this.heading) return this.bodyUnder(this.heading)
    const [, ...rest] = this.text.split("\n")
    return rest.join("\n").trim()
  }

  private static headingText(line: string): string {
    const match = HEADING_LINE.exec(line)
    return match?.[1] ?? match?.[2] ?? match?.[3] ?? line
  }

  /** Markdown markers stripped; underscores are emphasis only at a word edge, inside a name they are the name. */
  private static plain(line: string, maxLength = MAX_LABEL_LENGTH): string {
    return line
      .replace(/[*`]/g, "")
      .replace(/(^|\W)_+|_+(?=\W|$)/g, "$1")
      .trim()
      .slice(0, maxLength)
  }
}
