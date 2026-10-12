// Class strings must use Macaw's named scales (@langchain/macaw-components/docs/STYLES.md):
// spacing that lands on the 4px scale uses `*-space-N`, stacking uses the
// named `zIndices` layers, motion uses `duration-fast|normal|slow|slower`, and
// type uses the `text-xxs…` scale instead of pixel sizes.

const SPACE_STEP = { 1: 1, 2: 2, 3: 3, 4: 4, 6: 5, 8: 6, 10: 7, 12: 8, 16: 9 }
const SPACING =
  /(?<![\w[/.-])-?(?:p[xytblrse]?|m[xytblrse]?|gap(?:-[xy])?|space-[xy])-(\d+)(?![\w.[/%-])/g
const RAW = [
  [
    /(?<![\w-])z-(?:\d+|\[[^\]]+\])/g,
    "use a named z-index layer (z-popover, z-pane, …)",
  ],
  [/(?<![\w-])duration-\d+(?![\w[])/g, "use duration-fast|normal|slow|slower"],
  [/(?<![\w-])text-\[[\d.]+(?:px|rem)\]/g, "use the text-xxs…text-3xl scale"],
]

function problems(text) {
  const found = []
  for (const match of text.matchAll(SPACING)) {
    const step = SPACE_STEP[match[1]]
    if (step)
      found.push(
        `\`${match[0]}\`: use \`${match[0].replace(/-\d+$/, `-space-${step}`)}\``
      )
  }
  for (const [pattern, hint] of RAW) {
    for (const match of text.matchAll(pattern))
      found.push(`\`${match[0]}\`: ${hint}`)
  }
  return found
}

function check(context, node, text) {
  for (const message of problems(text)) context.report({ node, message })
}

export default {
  meta: { name: "macaw" },
  rules: {
    "no-raw-tokens": {
      meta: { type: "suggestion" },
      create(context) {
        return {
          Literal(node) {
            if (typeof node.value === "string") check(context, node, node.value)
          },
          TemplateElement(node) {
            check(context, node, node.value.raw)
          },
        }
      },
    },
  },
}
