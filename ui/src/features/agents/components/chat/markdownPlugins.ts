/**
 * Remark plugins for chat markdown.
 *
 * `remarkGithubAlerts` lifts GitHub's `> [!NOTE]` markers off the mdast into a
 * `data-alert` attribute for the blockquote renderer; `remarkNormalizeListItemIndentation`
 * rescues list items whose accidental over-indentation CommonMark reads as code.
 */

interface MarkdownAstNode {
  type?: string
  value?: unknown
  position?: { start?: { line?: number; offset?: number } }
  data?: { hProperties?: Record<string, unknown> }
  children?: MarkdownAstNode[]
}

interface MarkdownParser {
  parse(markdown: string): unknown
}

const GITHUB_ALERT_MARKER =
  /^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\](?:\r?\n|$)/i

function readGithubAlert(node: MarkdownAstNode): void {
  if (node.type !== "blockquote") return
  const paragraph = node.children?.[0]
  const text = paragraph?.children?.[0]
  if (
    paragraph?.type !== "paragraph" ||
    text?.type !== "text" ||
    typeof text.value !== "string"
  ) {
    return
  }
  const match = GITHUB_ALERT_MARKER.exec(text.value)
  if (!match?.[1]) return

  const remainder = text.value.slice(match[0].length)
  const markerEndsItsLine = match[0].endsWith("\n")
  if (remainder.length > 0) {
    text.value = remainder
  } else if (markerEndsItsLine || paragraph.children?.length === 1) {
    paragraph.children?.shift()
    if (paragraph.children?.length === 0) node.children?.shift()
  } else {
    // Something shares the marker's line — `[!NOTE]*aside*` — which is not an alert.
    return
  }

  node.data = {
    ...node.data,
    hProperties: {
      ...node.data?.hProperties,
      dataAlert: match[1].toLowerCase(),
    },
  }
}

export function remarkGithubAlerts() {
  return (tree: MarkdownAstNode) => {
    const visit = (node: MarkdownAstNode) => {
      node.children?.forEach(visit)
      readGithubAlert(node)
    }
    visit(tree)
  }
}

const INLINE_PARSE_PREFIX = "open-swe-markdown-inline-prefix:"

function isSameLineOverIndentedCode(
  node: MarkdownAstNode,
  parent: MarkdownAstNode | undefined,
  markdown: string
): boolean {
  if (
    node.type !== "code" ||
    parent?.type !== "listItem" ||
    typeof node.value !== "string" ||
    !/^[\t ]/.test(node.value)
  ) {
    return false
  }
  const nodeStart = node.position?.start
  const parentStart = parent.position?.start
  if (
    nodeStart?.line === undefined ||
    nodeStart.offset === undefined ||
    parentStart?.line === undefined ||
    nodeStart.line !== parentStart.line
  ) {
    return false
  }
  const sourceCharacter = markdown[nodeStart.offset]
  return sourceCharacter !== "`" && sourceCharacter !== "~"
}

/**
 * A text prefix forces block-looking input back into a paragraph while keeping the
 * processor's inline extensions; later root children stay blocks so blank-line
 * separated content is never discarded.
 */
function parseRecoveredMarkdown(
  value: string,
  parser: MarkdownParser
): { blocks: MarkdownAstNode[]; source: string } {
  const source = `${INLINE_PARSE_PREFIX}${value}`
  const document = parser.parse(source) as MarkdownAstNode
  const blocks = document.children
  const paragraph = blocks?.[0]
  const children =
    paragraph?.type === "paragraph" ? paragraph.children : undefined
  const first = children?.[0]
  if (
    !blocks ||
    !children ||
    first?.type !== "text" ||
    typeof first.value !== "string" ||
    !first.value.startsWith(INLINE_PARSE_PREFIX)
  ) {
    return { blocks: [{ type: "text", value }], source }
  }

  const firstValue = first.value.slice(INLINE_PARSE_PREFIX.length)
  return {
    blocks: [
      {
        ...paragraph,
        type: "paragraph",
        children: [
          ...(firstValue ? [{ ...first, value: firstValue }] : []),
          ...children.slice(1),
        ],
      },
      ...blocks.slice(1),
    ],
    source,
  }
}

/**
 * CommonMark treats four or more spaces after a list marker as an indented code
 * block. In agent output that spacing is usually accidental alignment such as
 * `-       text`, which otherwise renders a code card for every bullet. Only
 * blocks starting on the marker's own line are normalized; explicit fences and
 * conventional indented blocks stay code.
 */
export function remarkNormalizeListItemIndentation(this: MarkdownParser) {
  return (tree: MarkdownAstNode, file: { value?: unknown }) => {
    if (typeof file.value !== "string") return
    const visit = (node: MarkdownAstNode, source: string) => {
      if (!node.children) return
      node.children = node.children.flatMap((child) => {
        if (isSameLineOverIndentedCode(child, node, source)) {
          const value =
            typeof child.value === "string" ? child.value.trim() : ""
          const recovered = parseRecoveredMarkdown(value, this)
          const first = recovered.blocks[0]
          const blocks =
            first && child.position
              ? [
                  { ...first, position: child.position },
                  ...recovered.blocks.slice(1),
                ]
              : recovered.blocks
          for (const block of blocks) visit(block, recovered.source)
          return blocks
        }
        visit(child, source)
        return [child]
      })
    }
    visit(tree, file.value)
  }
}

/**
 * The default marker gutter fits two-character markers. Once a marker reaches
 * three characters (item 100+), `list-style-position: outside` paints it wider
 * than the gutter and clips the leading digit, so only those lists widen.
 */
export function orderedListGutterStyle(
  itemCount: number,
  start: unknown
): { "--list-gutter": string } | undefined {
  const parsedStart = Number.parseInt(String(start ?? 1), 10)
  const firstNumber = Number.isNaN(parsedStart) ? 1 : parsedStart
  const lastNumber = firstNumber + Math.max(itemCount - 1, 0)
  const markerWidth = Math.max(
    String(firstNumber).length,
    String(lastNumber).length
  )
  if (markerWidth <= 2) return undefined
  return { "--list-gutter": `${markerWidth + 1}ch` }
}
