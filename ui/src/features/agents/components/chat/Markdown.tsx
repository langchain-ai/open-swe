import { Component, createElement, memo, useMemo } from "react"
import {
  Streamdown,
  defaultRemarkPlugins,
  defaultUrlTransform,
} from "streamdown"
import type { ComponentProps, ReactNode } from "react"
import type { Components, ExtraProps } from "streamdown"
import {
  Info,
  Lightbulb,
  MessageSquareWarning,
  OctagonAlert,
  TriangleAlert,
} from "lucide-react"
import "streamdown/styles.css"
import { CodeBlock } from "./CodeBlock"
import {
  orderedListGutterStyle,
  remarkGithubAlerts,
  remarkNormalizeListItemIndentation,
} from "./markdownPlugins"

interface MarkdownProps {
  content: string
  /** When true, keep Streamdown in streaming mode for the duration of the run. */
  isLive?: boolean
  /**
   * Rewrite image `src` URLs before rendering (e.g. route private GitHub
   * attachments through an authenticated proxy). Return the URL unchanged to
   * leave it as-is.
   */
  transformImageUrl?: (src: string) => string
}

/**
 * Must stay referentially and structurally stable while a message is streaming.
 * Streamdown keys its animate plugin on JSON.stringify(animated); changing
 * stagger/duration recreates the plugin, resets prevContentLength, and
 * re-animates text that was already on screen.
 */
const STREAMDOWN_ANIMATED = {
  sep: "word",
  animation: "slideUp",
  duration: 60,
  stagger: 10,
  easing: "ease-out",
} as const

const REMARK_PLUGINS = [
  ...Object.values(defaultRemarkPlugins),
  remarkGithubAlerts,
  remarkNormalizeListItemIndentation,
]

/** `data-alert` is set by remarkGithubAlerts and would otherwise be sanitized away. */
const ALLOWED_TAGS = { blockquote: ["cite", "dataAlert"] }

const SHIKI_THEME: ["github-light", "github-dark"] = [
  "github-light",
  "github-dark",
]

/** GitHub's own five alert kinds; the colours live in styles/markdown.css. */
const ALERTS: Record<string, { label: string; Icon: typeof Info }> = {
  note: { label: "Note", Icon: Info },
  tip: { label: "Tip", Icon: Lightbulb },
  important: { label: "Important", Icon: MessageSquareWarning },
  warning: { label: "Warning", Icon: TriangleAlert },
  caution: { label: "Caution", Icon: OctagonAlert },
}

/**
 * Streamdown's defaults dress every element in utility classes sized for a
 * documentation page; chat markdown is styled by `.chat-markdown` instead, so
 * these renderers emit the plain element and drop the hast node prop.
 */
function passthrough(tag: string) {
  return function PassthroughElement({ node: _node, ...props }: ExtraProps) {
    return createElement(tag, props)
  }
}

type HastElement = NonNullable<ExtraProps["node"]>

const FENCE_TITLE_ATTR =
  /(?:^|\s)(?:title|file(?:name)?)=(?:"([^"]+)"|'([^']+)'|(\S+))/i
const FENCE_FILENAME_TOKEN = /^[\w@][\w@./-]*\.[A-Za-z0-9]+$/

/** Pulls a filename out of fence meta: ```ts title="x.ts" / ```ts src/main.ts */
function fenceTitle(meta: string | undefined): string | null {
  if (!meta) return null
  const attr = FENCE_TITLE_ATTR.exec(meta)
  return (
    attr?.[1] ??
    attr?.[2] ??
    attr?.[3] ??
    meta.split(/\s+/).find((token) => FENCE_FILENAME_TOKEN.test(token)) ??
    null
  )
}

/** Streamdown leaves syntax highlighting to the host, so fences render through CodeBlock. */
function fencedCode(node: HastElement | undefined) {
  const code = node?.children.find(
    (child) => child.type === "element" && child.tagName === "code"
  )
  if (code?.type !== "element") return null
  const { className, metastring } = code.properties
  const languageClass = Array.isArray(className)
    ? className.join(" ")
    : String(className ?? "")
  return {
    text: code.children
      .map((child) => (child.type === "text" ? child.value : ""))
      .join(""),
    language: /language-([^\s]+)/.exec(languageClass)?.[1],
    title: fenceTitle(typeof metastring === "string" ? metastring : undefined),
  }
}

const COMPONENTS: Components = {
  h1: passthrough("h1"),
  h2: passthrough("h2"),
  h3: passthrough("h3"),
  h4: passthrough("h4"),
  h5: passthrough("h5"),
  h6: passthrough("h6"),
  p: passthrough("p"),
  ul: passthrough("ul"),
  li: passthrough("li"),
  hr: passthrough("hr"),
  strong: passthrough("strong"),
  em: passthrough("em"),
  del: passthrough("del"),
  pre: ({ node, children, ...props }: ExtraProps & ComponentProps<"pre">) => {
    const fence = fencedCode(node)
    if (!fence) return <pre {...props}>{children}</pre>
    return (
      <CodeBlock
        text={fence.text}
        language={fence.language}
        title={fence.title}
      />
    )
  },
  ol: ({ node, ...props }: ExtraProps & ComponentProps<"ol">) => {
    const itemCount =
      node?.children.filter(
        (child) => child.type === "element" && child.tagName === "li"
      ).length ?? 0
    const gutter = orderedListGutterStyle(itemCount, props.start)
    return (
      <ol
        {...props}
        style={gutter ? { ...props.style, ...gutter } : props.style}
      />
    )
  },
  blockquote: ({
    node: _node,
    children,
    ...props
  }: ExtraProps & ComponentProps<"blockquote"> & { "data-alert"?: string }) => {
    const alert = ALERTS[props["data-alert"] ?? ""]
    // Not a <blockquote>: an alert's body is ordinary text under a coloured
    // title rather than a muted quote.
    if (!alert) return <blockquote {...props}>{children}</blockquote>
    return (
      <div role="note" data-alert={props["data-alert"]}>
        <p>
          <alert.Icon aria-hidden className="size-3.5 shrink-0" />
          {alert.label}
        </p>
        {children}
      </div>
    )
  },
  table: ({
    node: _node,
    children,
    ...props
  }: ExtraProps & ComponentProps<"table">) => (
    <div className="chat-markdown-table">
      <table {...props}>{children}</table>
    </div>
  ),
  inlineCode: ({
    node: _node,
    ...props
  }: ExtraProps & ComponentProps<"code">) => <code {...props} />,
  img: ({ src, alt }: ExtraProps & ComponentProps<"img">) => (
    <img
      src={typeof src === "string" ? src : undefined}
      alt={alt ?? ""}
      loading="lazy"
      className="border border-border/60"
    />
  ),
  a: ({
    node: _node,
    children,
    ...props
  }: ExtraProps & ComponentProps<"a">) => (
    <a {...props} target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
}

interface BoundaryProps {
  content: string
  children: ReactNode
}

interface BoundaryState {
  failed: boolean
  key: string
}

// Streamdown bundles Mermaid and renders ```mermaid blocks itself; a diagram it
// can't parse throws during render and, with no boundary, white-screens the
// whole page. Contain it and fall back to the raw markdown text.
class MarkdownErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { failed: false, key: this.props.content }

  static getDerivedStateFromError(): Partial<BoundaryState> {
    return { failed: true }
  }

  static getDerivedStateFromProps(
    props: BoundaryProps,
    state: BoundaryState
  ): Partial<BoundaryState> | null {
    if (props.content !== state.key)
      return { failed: false, key: props.content }
    return null
  }

  render(): ReactNode {
    if (this.state.failed) {
      return (
        <pre className="font-sans [overflow-wrap:anywhere] break-words whitespace-pre-wrap text-foreground">
          {this.props.content}
        </pre>
      )
    }
    return this.props.children
  }
}

export const Markdown = memo(function Markdown({
  content,
  isLive = false,
  transformImageUrl,
}: MarkdownProps) {
  const urlTransform = useMemo(() => {
    if (!transformImageUrl) return undefined
    return (
      url: string,
      key: string,
      node: Parameters<typeof defaultUrlTransform>[2]
    ) =>
      key === "src"
        ? transformImageUrl(url)
        : defaultUrlTransform(url, key, node)
  }, [transformImageUrl])

  return (
    <div className="chat-markdown max-w-full min-w-0 text-[14px] leading-[1.6] [overflow-wrap:anywhere] break-words text-foreground">
      <MarkdownErrorBoundary content={content}>
        <Streamdown
          mode={isLive ? "streaming" : "static"}
          parseIncompleteMarkdown={isLive}
          isAnimating={isLive}
          animated={isLive ? STREAMDOWN_ANIMATED : false}
          shikiTheme={SHIKI_THEME}
          className="streamdown-agent max-w-full min-w-0 space-y-0"
          components={COMPONENTS}
          remarkPlugins={REMARK_PLUGINS}
          allowedTags={ALLOWED_TAGS}
          urlTransform={urlTransform}
        >
          {content}
        </Streamdown>
      </MarkdownErrorBoundary>
    </div>
  )
})
