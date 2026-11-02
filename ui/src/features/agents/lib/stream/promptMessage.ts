import type { ImageChunk } from "@/features/agents/lib/types"
import type { PassageReply } from "@/features/agents/utils/passageReply"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"

export function imageBlocks(images: ReadonlyArray<ImageChunk> = []) {
  return images.map((image) => ({
    type: "image" as const,
    base64: image.base64,
    mime_type: image.mimeType,
    ...(image.fileName ? { file_name: image.fileName } : {}),
  }))
}

/**
 * The human message a prompt bar submission becomes on the graph. Replies ride
 * beside the content for the server to add to the message's envelope.
 */
export function promptMessage(
  text: string,
  images: ReadonlyArray<ImageChunk> = [],
  replies: ReadonlyArray<PassageReply> = []
) {
  const trimmed = text.trim()
  return {
    type: "human" as const,
    content: [
      ...imageBlocks(images),
      ...(trimmed ? [{ type: "text" as const, text: trimmed }] : []),
    ],
    ...(replies.length ? { additional_kwargs: { replies } } : {}),
  }
}

/** Run model settings, distinguishing a picker action from an inherited choice. */
export function modelConfigurable(
  selection:
    | Partial<Record<keyof ModelSelection, string | null>>
    | null
    | undefined,
  selectionChanged = false
): Record<string, unknown> {
  if (!selection?.modelId || !selection.effort)
    return {
      model_selection: "auto",
      ...(selectionChanged ? { model_selection_changed: true } : {}),
    }
  return {
    agent_model_id: selection.modelId,
    agent_effort: selection.effort,
    model_selection: "explicit",
  }
}
