import {
  coerceMessageLikeToMessage,
  type BaseMessage,
} from "@langchain/core/messages"

/** Checkpoints own saved messages; the SDK supplies only the uncommitted tail. */
export class ThreadTranscript {
  private saved: BaseMessage[] = []
  private live = new Map<string, BaseMessage>()
  private knownIds = new Set<string>()
  private signatures = new Map<string, { json: string; message: BaseMessage }>()
  private step = -Infinity
  private snapshot: BaseMessage[] = []

  checkpoint(messages: unknown[], step?: number, settled = false): void {
    if (step != null && step < this.step) return
    if (step != null) this.step = step
    this.saved = messages.map((raw) => {
      const message = coerceMessageLikeToMessage(
        raw as Parameters<typeof coerceMessageLikeToMessage>[0]
      )
      if (!message.id) return message
      const json = JSON.stringify(message)
      const previous = this.signatures.get(message.id)
      if (previous?.json === json) return previous.message
      this.signatures.set(message.id, { json, message })
      return message
    })
    const savedIds = new Set(
      this.saved.flatMap((message) => (message.id ? [message.id] : []))
    )
    for (const id of savedIds) this.knownIds.add(id)
    for (const id of this.signatures.keys())
      if (!savedIds.has(id)) this.signatures.delete(id)
    if (settled) this.live.clear()
    else for (const id of savedIds) this.live.delete(id)
    this.publish()
  }

  update(messages: BaseMessage[]): void {
    for (const message of messages) {
      if (!message.id || this.knownIds.has(message.id)) continue
      const previous = this.live.get(message.id)
      // Message deltas append text. A shorter prefix is assembler replay;
      // actual edits and removals arrive through checkpoint().
      if (
        previous &&
        previous.text.length > message.text.length &&
        previous.text.startsWith(message.text)
      )
        continue
      this.live.set(message.id, message)
    }
    this.publish()
  }

  reject(ids: string[]): void {
    for (const id of ids) {
      this.live.delete(id)
      this.knownIds.add(id)
    }
    this.publish()
  }

  private publish(): void {
    const next = [...this.saved, ...this.live.values()]
    if (
      next.length === this.snapshot.length &&
      next.every((m, i) => m === this.snapshot[i])
    )
      return
    this.snapshot = next
  }

  get messages(): BaseMessage[] {
    return this.snapshot
  }
}
