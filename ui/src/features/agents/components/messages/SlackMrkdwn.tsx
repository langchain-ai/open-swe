import { Fragment } from "react"
import type { ReactNode } from "react"

const ALLOWED_PROTOCOLS = new Set(["http:", "https:", "mailto:", "tel:"])
const LINK_CLASS =
  "text-foreground/90 underline decoration-foreground/40 break-words [overflow-wrap:anywhere]"

function decodeSlackText(text: string): string {
  return text.replace(/&(?:amp|lt|gt);/g, (entity) => {
    if (entity === "&amp;") return "&"
    if (entity === "&lt;") return "<"
    return ">"
  })
}

function safeHref(target: string): string | null {
  try {
    const url = new URL(target)
    return ALLOWED_PROTOCOLS.has(url.protocol) ? target : null
  } catch {
    return null
  }
}

function closingDelimiter(
  text: string,
  delimiter: string,
  start: number,
  end: number
): number {
  if (delimiter === "`") {
    const closing = text.indexOf(delimiter, start)
    if (closing === -1 || closing >= end) return -1
    return text.slice(start, closing).includes("\n") ? -1 : closing
  }

  let cursor = start
  while (cursor < end) {
    const character = text[cursor]
    if (character === "\n") return -1
    if (character === "`") {
      const codeEnd = text.indexOf("`", cursor + 1)
      if (codeEnd !== -1 && codeEnd < end) {
        cursor = codeEnd + 1
        continue
      }
    }
    if (character === "<") {
      const tokenEnd = text.indexOf(">", cursor + 1)
      if (
        tokenEnd !== -1 &&
        tokenEnd < end &&
        !text.slice(cursor + 1, tokenEnd).includes("\n")
      ) {
        cursor = tokenEnd + 1
        continue
      }
    }
    if (character === delimiter) return cursor > start ? cursor : -1
    cursor += 1
  }
  return -1
}

function slackTokenNode(token: string, key: string): ReactNode {
  const separator = token.indexOf("|")
  const rawTarget = separator === -1 ? token : token.slice(0, separator)
  const rawLabel = separator === -1 ? "" : token.slice(separator + 1)
  const target = decodeSlackText(rawTarget)
  const label = decodeSlackText(rawLabel)

  if (target.startsWith("@") || target.startsWith("#")) {
    const sigil = target[0]
    const displayLabel = (label || target.slice(1)).replace(/^[@#]/, "")
    return (
      <span key={key} className="text-foreground/90">
        {sigil}
        {displayLabel}
      </span>
    )
  }

  if (target.startsWith("!date^")) {
    return <Fragment key={key}>{label || target}</Fragment>
  }

  if (target.startsWith("!subteam^")) {
    return (
      <span key={key} className="text-foreground/90">
        {label || "@subteam"}
      </span>
    )
  }

  if (target.startsWith("!")) {
    const displayLabel = label || target.slice(1)
    return (
      <span key={key} className="text-foreground/90">
        {displayLabel.startsWith("@") ? displayLabel : `@${displayLabel}`}
      </span>
    )
  }

  const href = safeHref(target)
  if (!href) {
    const fallback = rawLabel || `&lt;${rawTarget}&gt;`
    return (
      <Fragment key={key}>
        {renderRange(fallback, 0, fallback.length, `${key}-fallback`)}
      </Fragment>
    )
  }

  const linkText = rawLabel || rawTarget
  return (
    <a
      key={key}
      href={href}
      target="_blank"
      rel="noreferrer"
      className={LINK_CLASS}
    >
      {renderRange(linkText, 0, linkText.length, `${key}-label`)}
    </a>
  )
}

function renderRange(
  text: string,
  start: number,
  end: number,
  keyPrefix: string
): Array<ReactNode> {
  const nodes: Array<ReactNode> = []
  let cursor = start
  let literalStart = start

  const flushLiteral = (until: number) => {
    if (until > literalStart) {
      nodes.push(decodeSlackText(text.slice(literalStart, until)))
    }
  }

  while (cursor < end) {
    const character = text[cursor]
    const key = `${keyPrefix}-${cursor}`

    if (character === "`") {
      const closing = closingDelimiter(text, "`", cursor + 1, end)
      if (closing !== -1) {
        flushLiteral(cursor)
        nodes.push(
          <code key={key} className="rounded bg-background/60 px-1 font-mono">
            {decodeSlackText(text.slice(cursor + 1, closing))}
          </code>
        )
        cursor = closing + 1
        literalStart = cursor
        continue
      }
    }

    if (character === "*" || character === "_" || character === "~") {
      const closing = closingDelimiter(text, character, cursor + 1, end)
      if (closing !== -1) {
        flushLiteral(cursor)
        const children = renderRange(text, cursor + 1, closing, `${key}-format`)
        if (character === "*") nodes.push(<strong key={key}>{children}</strong>)
        else if (character === "_") nodes.push(<em key={key}>{children}</em>)
        else nodes.push(<s key={key}>{children}</s>)
        cursor = closing + 1
        literalStart = cursor
        continue
      }
    }

    if (character === "<") {
      const closing = text.indexOf(">", cursor + 1)
      if (
        closing !== -1 &&
        closing < end &&
        !text.slice(cursor + 1, closing).includes("\n")
      ) {
        flushLiteral(cursor)
        nodes.push(slackTokenNode(text.slice(cursor + 1, closing), key))
        cursor = closing + 1
        literalStart = cursor
        continue
      }
    }

    cursor += 1
  }

  flushLiteral(end)
  return nodes
}

export function renderSlackMrkdwn(text: string): Array<ReactNode> {
  return renderRange(text, 0, text.length, "slack")
}

export function SlackMrkdwn({ text }: { text: string }) {
  return <>{renderSlackMrkdwn(text)}</>
}
