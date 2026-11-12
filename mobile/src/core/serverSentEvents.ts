/** One frame of a `text/event-stream` response. */
export interface ServerSentEvent {
  name: string
  data: string
}

/**
 * Assembles frames from a stream's text: `event:` and `data:` fields, ended by
 * a blank line; `:` lines are comments (the server's pings). Text can arrive
 * split anywhere, so a partial line waits for the rest.
 */
export class ServerSentEventParser {
  private buffer = ""
  private name = ""
  private data: string[] = []

  /** Whether every frame received so far has been returned, with none half-read. */
  get isBetweenFrames(): boolean {
    return this.buffer === "" && this.name === "" && this.data.length === 0
  }

  /** Feed newly received text; returns the frames it completed. */
  push(chunk: string): ServerSentEvent[] {
    this.buffer += chunk
    const frames: ServerSentEvent[] = []
    let newline = this.buffer.indexOf("\n")
    while (newline !== -1) {
      let line = this.buffer.slice(0, newline)
      this.buffer = this.buffer.slice(newline + 1)
      if (line.endsWith("\r")) line = line.slice(0, -1)
      const frame = this.consume(line)
      if (frame) frames.push(frame)
      newline = this.buffer.indexOf("\n")
    }
    return frames
  }

  private consume(line: string): ServerSentEvent | null {
    if (line === "") {
      const frame =
        this.name || this.data.length
          ? { name: this.name || "message", data: this.data.join("\n") }
          : null
      this.name = ""
      this.data = []
      return frame
    }
    if (line.startsWith(":")) return null
    const colon = line.indexOf(":")
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? "" : line.slice(colon + 1)
    if (value.startsWith(" ")) value = value.slice(1)
    if (field === "event") this.name = value
    else if (field === "data") this.data.push(value)
    return null
  }
}
