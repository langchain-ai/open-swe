export interface ElementContext {
  url: string
  title: string
  selector: string
  tag: string
  id: string
  classes: string[]
  attributes: Record<string, string>
  text: string
  bounds: { x: number; y: number; width: number; height: number }
  viewport: { width: number; height: number }
  reactComponents: string[]
}

function selectorFor(element: Element): string {
  const parts: string[] = []
  for (let node: Element | null = element; node; node = node.parentElement) {
    let part = node.tagName.toLowerCase()
    if (node.id) {
      const id = `#${CSS.escape(node.id)}`
      if (document.querySelectorAll(id).length === 1) {
        parts.unshift(id)
        break
      }
    }
    const siblings = node.parentElement
      ? [...node.parentElement.children].filter(
          (sibling) => sibling.tagName === node.tagName
        )
      : []
    if (siblings.length > 1)
      part += `:nth-of-type(${siblings.indexOf(node) + 1})`
    parts.unshift(part)
  }
  return parts.join(" > ")
}

function reactComponents(element: Element): string[] {
  const key = Object.keys(element).find((name) =>
    name.startsWith("__reactFiber$")
  )
  if (!key) return []
  let fiber: unknown = Reflect.get(element, key)
  const names: string[] = []
  for (
    let depth = 0;
    fiber && typeof fiber === "object" && depth < 50;
    depth++
  ) {
    const type: unknown = Reflect.get(fiber, "type")
    if (typeof type === "function" || (type && typeof type === "object")) {
      const name: unknown =
        Reflect.get(type, "displayName") ?? Reflect.get(type, "name")
      if (typeof name === "string" && !names.includes(name)) names.push(name)
    }
    fiber = Reflect.get(fiber, "return")
  }
  return names.slice(0, 12)
}

export function captureElement(element: Element): ElementContext {
  const rect = element.getBoundingClientRect()
  const attributes: Record<string, string> = {}
  for (const name of [
    "role",
    "aria-label",
    "data-testid",
    "data-test",
    "data-qa",
    "data-cy",
    "data-component",
    "type",
  ]) {
    const value = element.getAttribute(name)
    if (value) attributes[name] = value.slice(0, 500)
  }
  const sensitive = element.closest(
    "input, textarea, [contenteditable], [data-private], [data-sensitive]"
  )
  const clone = element.cloneNode(true)
  if (clone instanceof Element) {
    clone
      .querySelectorAll(
        "input, textarea, [contenteditable], [data-private], [data-sensitive], script, style"
      )
      .forEach((node) => node.remove())
  }
  return {
    url: `${location.origin}${location.pathname}`,
    title: document.title,
    selector: selectorFor(element),
    tag: element.tagName.toLowerCase(),
    id: element.id,
    classes: [...element.classList],
    attributes,
    text: sensitive
      ? ""
      : (clone.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, 500),
    bounds: {
      x: Math.round(rect.x + scrollX),
      y: Math.round(rect.y + scrollY),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    },
    viewport: { width: innerWidth, height: innerHeight },
    reactComponents: reactComponents(element),
  }
}
