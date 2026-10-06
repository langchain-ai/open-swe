// The dashboard learns what went stale from the invalidation stream
// (ui/src/lib/invalidations), so a query declares `meta.invalidatedBy` topics
// instead of polling. Files that still poll are listed in .oxlintrc.json; that
// list only shrinks.

const MESSAGE =
  "Don't poll: declare the topics this query reads in `meta: { invalidatedBy: [...] }` " +
  "(ui/src/lib/invalidations/topics.ts) and invalidate them where the data is written."

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
  meta: { name: "invalidations" },
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
