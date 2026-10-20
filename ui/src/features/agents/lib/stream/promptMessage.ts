import type { MediaChunk } from "@/features/agents/lib/types"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"

export function mediaBlocks(attachments: ReadonlyArray<MediaChunk> = []) {
  return attachments.map((attachment) => ({
    type: attachment.kind,
    base64: attachment.base64,
    mime_type: attachment.mimeType,
    ...(attachment.fileName ? { file_name: attachment.fileName } : {}),
  }))
}

/** The human message a prompt bar submission becomes on the graph. */
export function promptMessage(
  text: string,
  attachments: ReadonlyArray<MediaChunk> = []
) {
  const trimmed = text.trim()
  return {
    type: "human" as const,
    content: [
      ...mediaBlocks(attachments),
      ...(trimmed ? [{ type: "text" as const, text: trimmed }] : []),
    ],
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
