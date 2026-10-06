import { ConfirmableAction } from "@langchain/gtm-platform-design-system/patterns/confirmable-action"

/**
 * Mounted only while a delete is being decided: the host names the thread, the
 * pattern owns Cancel, progress and an inline failure.
 */
export function DeleteThreadDialog({
  threadTitle,
  onConfirm,
  onDismiss,
  detail = "This cannot be undone.",
}: {
  threadTitle: string
  /** Resolves once the thread is gone; a rejection keeps the dialog open. */
  onConfirm: () => Promise<void>
  onDismiss: () => void
  detail?: string
}) {
  return (
    <ConfirmableAction
      title="Delete thread"
      description={`Delete "${threadTitle}"? ${detail}`}
      confirmLabel="Delete"
      onConfirm={onConfirm}
      onDismiss={onDismiss}
    />
  )
}
