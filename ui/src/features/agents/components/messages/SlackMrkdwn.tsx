import { useQueries, useQuery } from "@tanstack/react-query"
import { Fragment } from "react"
import { api } from "@/lib/api"
import type { ReactNode } from "react"

import { PreviewablePullRequestLink } from "@/features/agents/components/PullRequestPreview"

const ALLOWED_PROTOCOLS = new Set(["http:", "https:", "mailto:", "tel:"])
const USER_MENTION = /^@[UW][A-Z0-9]{2,}$/
const USER_TOKEN = /<@([UW][A-Z0-9]{2,})>/g
const MARKDOWN_TOKEN =
  /(```[\s\S]*?```|`[^`\n]*`)|<([^<>\s|]+)(?:\|([^<>\n]*))?>/g
const LINK_CLASS =
  "text-primary underline decoration-[color:var(--text-tertiary)] break-words [overflow-wrap:anywhere]"

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

function userNameQuery(userId: string) {
  return {
    queryKey: ["slackUserName", userId],
    queryFn: () => api.slackUserName(userId),
    staleTime: 60 * 60 * 1000,
  }
}

export function SlackUserMention({ userId }: { userId: string }) {
  const { data } = useQuery(userNameQuery(userId))
  return <span className="text-primary">@{data?.name || userId}</span>
}

function slackTokenLabel(target: string, label: string): string | null {
  if (target.startsWith("@") || target.startsWith("#")) {
    return target[0] + (label || target.slice(1)).replace(/^[@#]/, "")
  }
  if (target.startsWith("!date^")) return label || target
  if (target.startsWith("!subteam^")) return label || "@subteam"
  if (!target.startsWith("!")) return null
  const name = label || target.slice(1)
  return name.startsWith("@") ? name : `@${name}`
}

/** Slack tokens in an outbound Markdown reply rewritten as Markdown, with mentions resolved. */
export function useSlackMarkdown(text: string): string {
  const ids = [
    ...new Set(Array.from(text.matchAll(USER_TOKEN), (m) => m[1] ?? "")),
  ]
  const names = useQueries({
    queries: ids.map((id) => userNameQuery(id)),
  })
  return text.replace(
    MARKDOWN_TOKEN,
    (match: string, code?: string, target = "", label = "") => {
      if (code) return match
      if (!label && USER_MENTION.test(target)) {
        return `@${names[ids.indexOf(target.slice(1))]?.data?.name || target.slice(1)}`
      }
      const tokenLabel = slackTokenLabel(target, label)
      if (tokenLabel !== null) return tokenLabel
      return label && safeHref(target) ? `[${label}](${target})` : match
    }
  )
}

function slackTokenNode(token: string, key: string): ReactNode {
  const separator = token.indexOf("|")
  const rawTarget = separator === -1 ? token : token.slice(0, separator)
  const rawLabel = separator === -1 ? "" : token.slice(separator + 1)
  const target = decodeSlackText(rawTarget)
  const label = decodeSlackText(rawLabel)

  if (!label && USER_MENTION.test(target)) {
    return <SlackUserMention key={key} userId={target.slice(1)} />
  }

  const tokenLabel = slackTokenLabel(target, label)
  if (tokenLabel !== null) {
    return (
      <span key={key} className="text-primary">
        {tokenLabel}
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
    <PreviewablePullRequestLink
      key={key}
      href={href}
      target="_blank"
      rel="noreferrer"
      className={LINK_CLASS}
    >
      {renderRange(linkText, 0, linkText.length, `${key}-label`)}
    </PreviewablePullRequestLink>
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
          <code
            key={key}
            className="rounded-xs bg-surface-level-3 px-space-1 font-mono"
          >
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
