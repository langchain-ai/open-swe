import {
  Linking,
  ScrollView,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type TextStyle,
} from "react-native"

import { Card } from "./components"
import { Theme } from "./theme"

export type Block =
  | { kind: "paragraph"; text: string }
  | { kind: "heading"; text: string }
  | { kind: "list"; items: string[]; ordered: boolean }
  | { kind: "code"; text: string }

/** Split agent markdown into paragraphs, headings, bullet and numbered lists, and code blocks. */
export function markdownBlocks(source: string): Block[] {
  const blocks: Block[] = []
  let paragraph: string[] = []
  let listItems: string[] = []
  let listOrdered = false
  let code: string[] | null = null

  const flushParagraph = () => {
    if (paragraph.length)
      blocks.push({ kind: "paragraph", text: paragraph.join("\n") })
    paragraph = []
  }
  const flushList = () => {
    if (listItems.length)
      blocks.push({ kind: "list", items: listItems, ordered: listOrdered })
    listItems = []
  }

  for (const line of source.split("\n")) {
    const trimmed = line.trim()
    if (trimmed.startsWith("```")) {
      if (code) {
        blocks.push({ kind: "code", text: code.join("\n") })
        code = null
      } else {
        flushParagraph()
        flushList()
        code = []
      }
      continue
    }
    if (code) {
      code.push(line)
      continue
    }
    const bullet = /^[-*+] (.*)$/.exec(trimmed)
    const numbered = /^\d+[.)] (.*)$/.exec(trimmed)
    if (!trimmed) {
      flushParagraph()
      flushList()
    } else if (trimmed.startsWith("#")) {
      flushParagraph()
      flushList()
      blocks.push({ kind: "heading", text: trimmed.replace(/^#+/, "").trim() })
    } else if (bullet) {
      flushParagraph()
      if (listItems.length && listOrdered) flushList()
      listOrdered = false
      listItems.push(bullet[1])
    } else if (numbered) {
      flushParagraph()
      if (listItems.length && !listOrdered) flushList()
      listOrdered = true
      listItems.push(numbered[1])
    } else if (listItems.length && line.startsWith(" ")) {
      listItems[listItems.length - 1] += ` ${trimmed}`
    } else {
      flushList()
      paragraph.push(trimmed)
    }
  }
  if (code) blocks.push({ kind: "code", text: code.join("\n") })
  flushParagraph()
  flushList()
  return blocks
}

type Span =
  | { kind: "text"; text: string; style: InlineStyle }
  | { kind: "link"; text: string; url: string; style: InlineStyle }

interface InlineStyle {
  bold?: boolean
  italic?: boolean
  strike?: boolean
  code?: boolean
}

const INLINE =
  /(`[^`]+`)|(\*\*[^*]+\*\*|__[^_]+__)|(\*[^*\s][^*]*\*|_[^_\s][^_]*_)|(~~[^~]+~~)|(\[[^\]]+\]\([^)\s]+\))/

/** Inline markdown: bold, italic, strikethrough, code and links. */
export function inlineSpans(text: string, style: InlineStyle = {}): Span[] {
  const spans: Span[] = []
  let rest = text
  while (rest) {
    const match = INLINE.exec(rest)
    if (!match) {
      spans.push({ kind: "text", text: rest, style })
      break
    }
    if (match.index > 0)
      spans.push({ kind: "text", text: rest.slice(0, match.index), style })
    const token = match[0]
    if (match[1])
      spans.push({
        kind: "text",
        text: token.slice(1, -1),
        style: { ...style, code: true },
      })
    else if (match[2])
      spans.push(...inlineSpans(token.slice(2, -2), { ...style, bold: true }))
    else if (match[3])
      spans.push(...inlineSpans(token.slice(1, -1), { ...style, italic: true }))
    else if (match[4])
      spans.push(...inlineSpans(token.slice(2, -2), { ...style, strike: true }))
    else {
      const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(token)
      if (link) spans.push({ kind: "link", text: link[1], url: link[2], style })
    }
    rest = rest.slice(match.index + token.length)
  }
  return spans
}

function spanStyle(style: InlineStyle): TextStyle {
  return {
    fontWeight: style.bold ? "600" : undefined,
    fontStyle: style.italic ? "italic" : undefined,
    textDecorationLine: style.strike ? "line-through" : undefined,
    fontFamily: style.code ? "Menlo" : undefined,
    fontSize: style.code ? 15 : undefined,
  }
}

/** Inline-styled text; links open only when tapped. */
export function InlineText({
  text,
  style,
}: {
  text: string
  style?: StyleProp<TextStyle>
}) {
  return (
    <Text style={style}>
      {inlineSpans(text).map((span, index) =>
        span.kind === "link" ? (
          <Text
            key={index}
            style={[spanStyle(span.style), { color: Theme.link }]}
            onPress={() => {
              if (/^https?:\/\//i.test(span.url)) Linking.openURL(span.url)
            }}
          >
            {span.text}
          </Text>
        ) : (
          <Text key={index} style={spanStyle(span.style)}>
            {span.text}
          </Text>
        )
      )}
    </Text>
  )
}

export function MarkdownText({ source }: { source: string }) {
  return (
    <View style={{ gap: 10 }}>
      {markdownBlocks(source).map((block, index) => {
        switch (block.kind) {
          case "paragraph":
            return (
              <InlineText key={index} text={block.text} style={styles.body} />
            )
          case "heading":
            return (
              <InlineText
                key={index}
                text={block.text}
                style={styles.heading}
              />
            )
          case "list":
            return (
              <View key={index} style={{ gap: 8, paddingLeft: 4 }}>
                {block.items.map((item, itemIndex) => (
                  <View
                    key={itemIndex}
                    style={{ flexDirection: "row", gap: 12 }}
                  >
                    <Text
                      style={[
                        styles.body,
                        { color: Theme.textSecondary, minWidth: 14 },
                      ]}
                    >
                      {block.ordered ? `${itemIndex + 1}.` : "•"}
                    </Text>
                    <InlineText
                      text={item}
                      style={[styles.body, { flex: 1 }]}
                    />
                  </View>
                ))}
              </View>
            )
          case "code":
            return (
              <Card key={index} radius={12}>
                <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                  <Text style={styles.code}>{block.text}</Text>
                </ScrollView>
              </Card>
            )
        }
      })}
    </View>
  )
}

const styles = StyleSheet.create({
  body: { fontSize: 17, lineHeight: 23, color: Theme.textPrimary },
  heading: { fontSize: 18, fontWeight: "600", color: Theme.textPrimary },
  code: {
    fontFamily: "Menlo",
    fontSize: 13,
    color: Theme.textPrimary,
    padding: 12,
  },
})
