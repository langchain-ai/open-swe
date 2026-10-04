// The dashboard learns about changes from the live event stream
// (ui/src/lib/live), so a query declares `meta.live` topics instead of polling.
// Files that still poll are listed in .oxlintrc.json; that list only shrinks.

const MESSAGE =
  "Don't poll: declare the topics this query reads in `meta: { live: [...] }` " +
  "(ui/src/lib/live/topics.ts) and publish them where the data is written."

function propertyName(node) {
  if (node.computed) return null
  if (node.key.type === "Identifier") return node.key.name
  if (node.key.type === "Literal") return String(node.key.value)
  return null
}

function isSetInterval(callee) {
  if (callee.type === "Identifier") return callee.name === "setInterval"
  return (
    callee.type === "MemberExpression" &&
    !callee.computed &&
    callee.property.type === "Identifier" &&
    callee.property.name === "setInterval"
  )
}

export default {
  meta: { name: "live" },
  rules: {
    "no-polling": {
      meta: { type: "problem" },
      create(context) {
        return {
          Property(node) {
            if (propertyName(node) === "refetchInterval") {
              context.report({ node, message: MESSAGE })
            }
          },
          CallExpression(node) {
            if (isSetInterval(node.callee)) {
              context.report({ node, message: MESSAGE })
            }
          },
        }
      },
    },
  },
}
