/** Track the Auto picker action carried by one optimistic submission. */
export function createAutoSelectionIntent() {
  let pending = false
  let submittedMessageId: string | null = null

  return {
    select(auto: boolean) {
      pending = auto
      submittedMessageId = null
    },
    claim(messageId: string, eligible: boolean): boolean {
      if (!pending || !eligible) return false
      pending = false
      submittedMessageId = messageId
      return true
    },
    restore(messageId: string) {
      if (submittedMessageId !== messageId) return
      pending = true
      submittedMessageId = null
    },
  }
}
