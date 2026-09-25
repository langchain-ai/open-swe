import type { DiffData, OutputIframeDisplay } from "./types"

export function maybeDiffFromArgs(
  args: Record<string, unknown>
): DiffData | null {
  const path = args.path ?? args.file_path ?? args.target_file
  if (typeof path !== "string" || !path.trim()) return null
  const oldContent = args.old_string ?? args.original_content
  const newContent = args.new_string ?? args.content ?? args.new_content
  if (typeof newContent !== "string") return null
  const original = typeof oldContent === "string" ? oldContent : null
  return {
    originalContent: original,
    newContent,
    filePath: path.trim(),
    isNewFile: original === null,
    isBinary: false,
    isTruncated: false,
    totalLines: Math.max(newContent.split("\n").length, 1),
  }
}

function isHttpUrl(value: unknown): value is string {
  if (typeof value !== "string") return false
  try {
    const url = new URL(value)
    return url.protocol === "https:" || url.protocol === "http:"
  } catch {
    return false
  }
}

export function outputIframeDisplay(
  artifact: unknown
): OutputIframeDisplay | undefined {
  if (!artifact || typeof artifact !== "object" || Array.isArray(artifact)) {
    return undefined
  }
  const value = artifact as Record<string, unknown>
  if (
    value.type !== "output_iframe" ||
    typeof value.title !== "string" ||
    typeof value.filename !== "string"
  ) {
    return undefined
  }
  if (isHttpUrl(value.preview_url) && isHttpUrl(value.download_url)) {
    return {
      type: "output_iframe",
      previewUrl: value.preview_url,
      downloadUrl: value.download_url,
      title: value.title,
      filename: value.filename,
    }
  }
  if (typeof value.html === "string") {
    return {
      type: "output_iframe",
      html: value.html,
      title: value.title,
      filename: value.filename,
    }
  }
  return undefined
}
