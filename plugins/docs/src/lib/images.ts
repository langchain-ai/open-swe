import Image from "@tiptap/extension-image"

/** Loads an image file as a URL the frame can show; `null` when there is none. */
export type ImageLoader = (file: ImageFile) => Promise<string | null>

export interface ImageFile {
  path: string
  /** Another repository's, from a GitHub file URL; the page's own when absent. */
  repository?: string
  ref?: string
}

const GITHUB_FILE = [
  /^https:\/\/raw\.githubusercontent\.com\/([^/]+)\/([^/]+)\/([^/]+)\/(.+)$/,
  /^https:\/\/github\.com\/([^/]+)\/([^/]+)\/(?:raw|blob)\/([^/]+)\/(.+)$/,
]

/** The repository file an image `src` names: a path relative to the page, or a GitHub file URL. */
export function imageFile(src: string, page: string): ImageFile | null {
  const url = src.replace(/[?#].*$/, "")
  for (const pattern of GITHUB_FILE) {
    const match = pattern.exec(url)
    if (match) {
      const [, owner = "", repo = "", ref = "", path = ""] = match
      return {
        repository: `${owner}/${repo}`.toLowerCase(),
        ref,
        path: decodeURIComponent(path),
      }
    }
  }
  if (/^[a-z][a-z0-9+.-]*:/i.test(url) || url.startsWith("//")) return null
  const parts = url.startsWith("/") ? [] : page.split("/").slice(0, -1)
  for (const part of url.split("/")) {
    if (!part || part === ".") continue
    if (part === "..") parts.pop()
    else parts.push(decodeURIComponent(part))
  }
  return parts.length ? { path: parts.join("/") } : null
}

/**
 * Images as GitHub shows them, for files in a repository the person shared:
 * relative paths and GitHub file URLs load through their GitHub access. The
 * frame loads nothing itself, so any other image shows its alt text.
 */
export function repositoryImages(page: () => string, load: () => ImageLoader) {
  return Image.extend({
    addNodeView() {
      return ({ node }) => {
        const img = document.createElement("img")
        const src = String(node.attrs.src ?? "")
        if (node.attrs.alt) img.alt = String(node.attrs.alt)
        if (node.attrs.title) img.title = String(node.attrs.title)
        const file = imageFile(src, page())
        if (file === null) img.src = src
        else
          void load()(file).then((url) => {
            if (url) img.src = url
          })
        return { dom: img }
      }
    },
  })
}
