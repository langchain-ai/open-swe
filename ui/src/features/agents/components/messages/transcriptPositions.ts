const STORAGE_KEY = "open-swe.transcript-positions.v1"
const MAX_POSITIONS = 200

export interface TranscriptPosition {
  top: number
  atBottom: boolean
  anchor: { messageId: string; offset: number; collapsedGroup?: boolean } | null
}

class TranscriptPositions {
  private positions: Map<string, TranscriptPosition> | null = null

  private load() {
    if (this.positions) return this.positions
    this.positions = new Map()
    if (typeof window === "undefined") return this.positions
    try {
      const stored: unknown = JSON.parse(
        window.localStorage.getItem(STORAGE_KEY) ?? "[]"
      )
      if (Array.isArray(stored)) {
        for (const entry of stored.slice(-MAX_POSITIONS)) {
          if (!Array.isArray(entry) || typeof entry[0] !== "string") continue
          const position: unknown = entry[1]
          if (
            typeof position !== "object" ||
            position === null ||
            !("top" in position) ||
            typeof position.top !== "number" ||
            !Number.isFinite(position.top) ||
            position.top < 0 ||
            !("atBottom" in position) ||
            typeof position.atBottom !== "boolean" ||
            !("anchor" in position)
          )
            continue
          const rawAnchor = position.anchor
          let anchor: TranscriptPosition["anchor"] = null
          if (rawAnchor !== null) {
            if (
              typeof rawAnchor !== "object" ||
              !("messageId" in rawAnchor) ||
              typeof rawAnchor.messageId !== "string" ||
              !("offset" in rawAnchor) ||
              typeof rawAnchor.offset !== "number" ||
              !Number.isFinite(rawAnchor.offset)
            )
              continue
            anchor = {
              messageId: rawAnchor.messageId,
              offset: rawAnchor.offset,
              ...("collapsedGroup" in rawAnchor &&
              rawAnchor.collapsedGroup === true
                ? { collapsedGroup: true }
                : {}),
            }
          }
          this.positions.set(entry[0], {
            top: position.top,
            atBottom: position.atBottom,
            anchor,
          })
        }
      }
    } catch (error) {
      console.warn("Could not read transcript positions", error)
    }
    return this.positions
  }

  read(key: string) {
    return this.load().get(key)
  }

  remember(key: string, position: TranscriptPosition) {
    const positions = this.load()
    positions.delete(key)
    positions.set(key, position)
    if (positions.size > MAX_POSITIONS) {
      const oldest = positions.keys().next().value
      if (oldest !== undefined) positions.delete(oldest)
    }
  }

  persist() {
    if (!this.positions || typeof window === "undefined") return
    try {
      window.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify([...this.positions])
      )
    } catch (error) {
      console.warn("Could not save transcript positions", error)
    }
  }

  capture(el: HTMLElement, atBottom: boolean): TranscriptPosition {
    if (atBottom) return { top: el.scrollTop, atBottom, anchor: null }
    const viewportTop = el.getBoundingClientRect().top
    let anchor: TranscriptPosition["anchor"] = null
    for (const message of el.querySelectorAll<HTMLElement>(
      "[data-message-id]"
    )) {
      const rect = message.getBoundingClientRect()
      if (rect.bottom > viewportTop) {
        anchor = {
          messageId: message.dataset.messageId!,
          offset: rect.top - viewportTop,
          ...(message.dataset.messageIds ? { collapsedGroup: true } : {}),
        }
        break
      }
    }
    return { top: el.scrollTop, atBottom, anchor }
  }

  target(el: HTMLElement, position: TranscriptPosition): number | null {
    if (!position.anchor) return position.top
    const messageId = position.anchor.messageId
    const exact = el.querySelector<HTMLElement>(
      `[data-message-id="${CSS.escape(messageId)}"]`
    )
    const message =
      exact ??
      [...el.querySelectorAll<HTMLElement>("[data-message-ids]")].find(
        (group) =>
          (JSON.parse(group.dataset.messageIds!) as string[]).includes(
            messageId
          )
      )
    if (!message) return null
    return (
      el.scrollTop +
      message.getBoundingClientRect().top -
      el.getBoundingClientRect().top -
      (!!message.dataset.messageIds === !!position.anchor.collapsedGroup
        ? position.anchor.offset
        : 0)
    )
  }
}

export const transcriptPositions = new TranscriptPositions()
