import CodeBlock from "@tiptap/extension-code-block"

let loaded: Promise<typeof import("mermaid").default> | null = null
let drawn = 0

/** Mermaid, loaded the first time a page has a diagram; it is inside the bundle, like everything. */
function mermaid() {
  loaded ??= import("mermaid").then(({ default: m }) => {
    m.initialize({ startOnLoad: false, securityLevel: "strict" })
    return m
  })
  return loaded
}

/** `source` drawn as SVG in the plugin's theme. */
async function draw(source: string): Promise<string> {
  const m = await mermaid()
  const dark = document.documentElement.dataset.theme === "dark"
  m.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: dark ? "dark" : "default",
  })
  const { svg } = await m.render(`docs-mermaid-${++drawn}`, source)
  return svg
}

/**
 * Code blocks, with ```mermaid ones drawn as their diagram. A published page
 * shows only the diagram; in a draft the code stays editable above it, and
 * the diagram follows each change.
 */
export const DiagramCodeBlock = CodeBlock.extend({
  addNodeView() {
    return ({ node: initial, editor }) => {
      let node = initial
      const dom = document.createElement("div")
      const pre = document.createElement("pre")
      const code = document.createElement("code")
      pre.append(code)
      dom.append(pre)
      let diagram: HTMLDivElement | null = null
      let timer: ReturnType<typeof setTimeout> | undefined

      const show = () => {
        const mermaidBlock = node.attrs.language === "mermaid"
        code.className = node.attrs.language
          ? `language-${node.attrs.language}`
          : ""
        pre.hidden = mermaidBlock && !editor.isEditable
        if (!mermaidBlock) {
          diagram?.remove()
          diagram = null
          return
        }
        if (!diagram) {
          diagram = document.createElement("div")
          diagram.className = "docs-diagram"
          diagram.contentEditable = "false"
          dom.append(diagram)
        }
        const target = diagram
        const source = node.textContent
        clearTimeout(timer)
        timer = setTimeout(() => {
          draw(source).then(
            (svg) => {
              target.innerHTML = svg
              target.classList.remove("docs-diagram-error")
            },
            (error: unknown) => {
              target.textContent = `This diagram doesn't draw: ${error instanceof Error ? error.message : String(error)}`
              target.classList.add("docs-diagram-error")
            }
          )
        }, 250)
      }
      show()

      return {
        dom,
        contentDOM: code,
        update(updated) {
          if (updated.type !== node.type) return false
          node = updated
          show()
          return true
        },
        ignoreMutation: (mutation) =>
          diagram !== null && diagram.contains(mutation.target),
        destroy: () => clearTimeout(timer),
      }
    }
  },
})
