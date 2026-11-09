/**
 * What a human message in a transcript shows, following the dashboard's rules
 * for the structured input the backend sends the model:
 *
 * - `<dynamic-context …>` messages are context for the model only; hidden.
 * - `<input-message sender="…">` wraps what someone wrote, beside data
 *   elements (`<issue>`, `<replying_to>`, …); the transcript shows the text.
 * - Messages from silent system senders steer the model; hidden.
 * - Anything else is shown as it is.
 */
const SILENT_SENDERS = new Set([
  "system:sender-context",
  "system:dashboard-handoff",
  "system:slack-note",
])
const CONTEXT = /^\s*<dynamic-context\b[^>]*>[\s\S]*?<\/dynamic-context>\s*$/
const MESSAGE = /^\s*<input-message\b([^>]*)>([\s\S]*?)<\/input-message>\s*$/
const SENDER = /\bsender\s*=\s*"([^"]*)"/
const OPEN_TAG = /^\s*<[A-Za-z_][\w:.-]*>/

/** The text to show, or null when the message is not for readers of the thread. */
export function shownText(raw: string): string | null {
  if (CONTEXT.test(raw)) return null
  const match = MESSAGE.exec(raw)
  if (!match) return visible(raw)
  const sender = SENDER.exec(match[1] ?? "")?.[1]
  if (sender && SILENT_SENDERS.has(sender)) return null
  return visible(decode(authoredText(match[2] ?? "")))
}

function visible(text: string): string | null {
  const trimmed = text.trim()
  return trimmed ? trimmed : null
}

/** The envelope's text without the data elements that follow it. */
function authoredText(body: string): string {
  const start = body.lastIndexOf("<content>")
  if (start !== -1) {
    const end = body.indexOf("</content>", start)
    if (end !== -1) return body.slice(start + "<content>".length, end)
  }
  if (OPEN_TAG.test(body)) return ""
  for (
    let index = body.indexOf("\n<");
    index !== -1;
    index = body.indexOf("\n<", index + 1)
  ) {
    if (OPEN_TAG.test(body.slice(index + 1))) return body.slice(0, index)
  }
  return body
}

function decode(text: string): string {
  return text
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&amp;", "&")
}
