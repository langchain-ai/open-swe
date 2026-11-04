import { pluginsApi } from "@/features/plugins/lib/api"
import type { AgentToolOffer, ToolRunner } from "@/features/plugins/lib/bridge"

interface ChatDelivery {
  busy: boolean
  paused: boolean
  undelivered: Map<string, string>
}

/** Results stay with their chat while the dock switches to another one. */
export class PluginChatDeliveries {
  #installation: string
  #chats = new Map<string, ChatDelivery>()
  #listeners = new Set<() => void>()

  constructor(installation: string) {
    this.#installation = installation
  }

  isPaused(chatId: string): boolean {
    return this.#chats.get(chatId)?.paused ?? false
  }

  subscribe(listener: () => void): () => void {
    this.#listeners.add(listener)
    return () => this.#listeners.delete(listener)
  }

  hasUndelivered(chatId: string): boolean {
    return Boolean(this.#chats.get(chatId)?.undelivered.size)
  }

  dismiss(chatId: string): void {
    const chat = this.#chats.get(chatId)
    if (chat) {
      chat.paused = false
      this.#changed()
    }
  }

  #changed(): void {
    for (const listener of this.#listeners) listener()
  }

  async answer(
    chatId: string,
    tools: { offer: AgentToolOffer; run: ToolRunner },
    name: string
  ): Promise<void> {
    let chat = this.#chats.get(chatId)
    if (!chat) {
      chat = { busy: false, paused: false, undelivered: new Map() }
      this.#chats.set(chatId, chat)
    }
    if (chat.busy) return
    chat.busy = true
    try {
      const offered = new Set(tools.offer.tools.map((tool) => tool.name))
      for (const call of await pluginsApi.claimCalls(
        this.#installation,
        chatId
      )) {
        if (chat.undelivered.has(call.call_id)) continue
        const output = offered.has(call.tool)
          ? await tools
              .run(call.tool, call.arguments)
              .catch(
                (error: unknown) =>
                  `Error: ${error instanceof Error ? error.message : String(error)}`
              )
          : `Error: ${name} does not offer ${call.tool} now.`
        chat.undelivered.set(call.call_id, output)
      }
      const outputs = [...chat.undelivered].map(([call_id, output]) => ({
        call_id,
        output,
      }))
      if (outputs.length === 0) return
      const sent = await pluginsApi.answerCalls(
        this.#installation,
        chatId,
        outputs
      )
      chat.undelivered.clear()
      chat.paused = sent.paused
      this.#changed()
    } finally {
      chat.busy = false
    }
  }
}
