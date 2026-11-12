import { useSyncExternalStore } from "react"

/** How the Files panel renders a file, decided by its extension. */
export type PreviewKind =
  | "markdown"
  | "html"
  | "image"
  | "svg"
  | "video"
  | "audio"
  | "pdf"
  | "table"
  | "notebook"
  | "document"
  | "spreadsheet"
  | "download"
  | "source"

export type PreviewMode = "rendered" | "source"

const KINDS: Readonly<Record<string, PreviewKind>> = {
  md: "markdown",
  markdown: "markdown",
  html: "html",
  htm: "html",
  png: "image",
  jpg: "image",
  jpeg: "image",
  gif: "image",
  webp: "image",
  avif: "image",
  bmp: "image",
  ico: "image",
  svg: "svg",
  mp4: "video",
  webm: "video",
  mov: "video",
  mp3: "audio",
  wav: "audio",
  ogg: "audio",
  m4a: "audio",
  pdf: "pdf",
  csv: "table",
  tsv: "table",
  ipynb: "notebook",
  docx: "document",
  xlsx: "spreadsheet",
  // Binary formats with no viewer here; other binaries are found by reading them.
  pptx: "download",
  ppt: "download",
  doc: "download",
  xls: "download",
  key: "download",
  numbers: "download",
  pages: "download",
}

const STORAGE_KEY = "open-swe.files.preview-mode.v1"
const listeners = new Set<() => void>()

export function previewKind(path: string): PreviewKind {
  const extension = /\.([^./]+)$/.exec(path)?.[1]?.toLowerCase() ?? ""
  return KINDS[extension] ?? "source"
}

const WITH_SOURCE: ReadonlySet<PreviewKind> = new Set([
  "markdown",
  "html",
  "svg",
  "table",
  "notebook",
])

/** Whether a kind also has a text source to switch to. */
export function hasSourceView(kind: PreviewKind): boolean {
  return WITH_SOURCE.has(kind)
}

function storedModes(): Readonly<Record<string, PreviewMode>> {
  try {
    const stored: unknown = JSON.parse(
      window.localStorage.getItem(STORAGE_KEY) ?? "{}"
    )
    return stored && typeof stored === "object"
      ? (stored as Record<string, PreviewMode>)
      : {}
  } catch {
    return {}
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** The rendered/source choice for a kind, remembered in this browser; rendered by default. */
export function usePreviewMode(
  kind: PreviewKind
): [PreviewMode, (mode: PreviewMode) => void] {
  const mode = useSyncExternalStore(
    subscribe,
    () => (storedModes()[kind] === "source" ? "source" : "rendered"),
    (): PreviewMode => "rendered"
  )
  const setMode = (next: PreviewMode) => {
    try {
      window.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ ...storedModes(), [kind]: next })
      )
    } catch {
      // Storage can be unavailable; the switch then has nothing to remember.
    }
    listeners.forEach((listener) => listener())
  }
  return [mode, setMode]
}

/** What a lazily loaded viewer is given. */
export interface ViewerProps {
  url: string
  path: string
  onOpenFile: (path: string) => void
}
