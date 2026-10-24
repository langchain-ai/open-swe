import Image from "@tiptap/extension-image"

/** Loads a repository image as a URL the frame can show; `null` when there is none. */
export type ImageLoader = (path: string) => Promise<string | null>

/** A `src` that names a file in the repository rather than a URL. */
function repositoryPath(src: string, page: string): string | null {
  if (/^[a-z][a-z0-9+.-]*:/i.test(src) || src.startsWith("//")) return null
  const base = src.startsWith("/") ? [] : page.split("/").slice(0, -1)
  const parts: string[] = [...base]
  for (const part of src.replace(/[?#].*$/, "").split("/")) {
    if (!part || part === ".") continue
    if (part === "..") parts.pop()
    else parts.push(decodeURIComponent(part))
  }
  return parts.length ? parts.join("/") : null
}

/**
 * Images as GitHub shows them: URLs load as they are (the frame allows
 * GitHub's image hosts), and paths in the repository, relative to the page,
 * load through the person's GitHub access.
 */
export function repositoryImages(page: () => string, load: () => ImageLoader) {
  return Image.extend({
    addNodeView() {
      return ({ node }) => {
        const img = document.createElement("img")
        const src = String(node.attrs.src ?? "")
        if (node.attrs.alt) img.alt = String(node.attrs.alt)
        if (node.attrs.title) img.title = String(node.attrs.title)
        const path = repositoryPath(src, page())
        if (path === null) img.src = src
        else
          void load()(path).then((url) => {
            if (url) img.src = url
          })
        return { dom: img }
      }
    },
  })
}
