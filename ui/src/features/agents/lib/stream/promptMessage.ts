import type { ImageChunk } from "@/features/agents/lib/types"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"

export function imageBlocks(images: ReadonlyArray<ImageChunk> = []) {
  return images.flatMap((image) => {
    const source = image.fileId
      ? { file_id: image.fileId }
      : image.base64
        ? { base64: image.base64 }
        : null
    if (!source) return []
    return [
      {
        type: "image" as const,
        ...source,
        mime_type: image.mimeType,
        ...(image.fileName ? { file_name: image.fileName } : {}),
      },
    ]
  })
}

/** The human message a prompt bar submission becomes on the graph. */
export function promptMessage(
  text: string,
  images: ReadonlyArray<ImageChunk> = []
) {
  const trimmed = text.trim()
  return {
    type: "human" as const,
    content: [
      ...imageBlocks(images),
      ...(trimmed ? [{ type: "text" as const, text: trimmed }] : []),
    ],
  }
}

/** Run `configurable` entries for the picked model, or none when unset. */
export function modelConfigurable(
  selection:
    | Partial<Record<keyof ModelSelection, string | null>>
    | null
    | undefined
): Record<string, unknown> {
  if (!selection?.modelId || !selection.effort)
    return { model_selection: "auto" }
  return {
    agent_model_id: selection.modelId,
    agent_effort: selection.effort,
    model_selection: "explicit",
  }
}
