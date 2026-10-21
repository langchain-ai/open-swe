/**
 * The live rooms one plugin frame has joined. The frame cannot open a socket,
 * so the web UI holds one SSE stream for all of them and hands what it
 * carries to each room's listener. One stream, not one per room: browsers give
 * a host only a few HTTP/1.1 connections, and every open tab needs its own.
 *
 * Joining or leaving a room reopens the stream after the last update each
 * room delivered, so the frame never misses one and never sees one twice.
 */
import type { RoomEvent } from "@openswe/plugin-sdk"

import type { RoomTarget } from "@/features/plugins/lib/api"
import { fromBase64, roomsStreamUrl } from "@/features/plugins/lib/api"
import { dashboardApiUrl } from "@/lib/dashboard-fetch"

const MAX_BACKOFF_MS = 15_000

interface Joined {
  target: RoomTarget
  seq: number
  onEvent: (event: RoomEvent) => void
}

function keyOf(room: string, repository: string | null | undefined): string {
  return `${repository ?? ""}#${room}`
}

interface RoomFrame {
  room: string
  repository: string | null
}

export class PluginRooms {
  private readonly joined = new Map<string, Joined>()
  private source: EventSource | null = null
  private reopen: ReturnType<typeof setTimeout> | null = null
  private backoff = 500
  private closed = false

  constructor(private readonly installationId: string) {}

  /** Streams `target` to `onEvent` from after `after`; returns the leave. */
  join(
    target: RoomTarget,
    after: number,
    onEvent: (event: RoomEvent) => void
  ): () => void {
    const key = keyOf(target.room, target.repository)
    const joined: Joined = { target, seq: after, onEvent }
    this.joined.set(key, joined)
    this.schedule(0)
    return () => {
      if (this.joined.get(key) !== joined) return
      this.joined.delete(key)
      this.schedule(0)
    }
  }

  closeAll(): void {
    this.closed = true
    if (this.reopen) clearTimeout(this.reopen)
    this.source?.close()
    this.joined.clear()
  }

  private schedule(delay: number): void {
    if (this.closed) return
    if (this.reopen) clearTimeout(this.reopen)
    this.reopen = setTimeout(() => this.open(), delay)
  }

  private open(): void {
    this.reopen = null
    this.source?.close()
    this.source = null
    if (this.joined.size === 0) return
    const source = new EventSource(
      dashboardApiUrl(
        roomsStreamUrl(
          this.installationId,
          [...this.joined.values()].map(({ target, seq }) => ({
            ...target,
            after: seq,
          }))
        )
      ),
      { withCredentials: true }
    )
    this.source = source
    const parse = <T extends RoomFrame>(message: MessageEvent<string>) => {
      const data = JSON.parse(message.data) as T
      const joined = this.joined.get(keyOf(data.room, data.repository))
      return joined ? { data, joined } : null
    }
    source.addEventListener("update", (message) => {
      const frame = parse<RoomFrame & { seq: number; update: string }>(message)
      if (!frame) return
      frame.joined.seq = Math.max(frame.joined.seq, frame.data.seq)
      frame.joined.onEvent({
        type: "update",
        seq: frame.data.seq,
        update: fromBase64(frame.data.update),
      })
    })
    source.addEventListener("synced", (message) => {
      const frame = parse<RoomFrame & { seq: number }>(message)
      if (!frame) return
      this.backoff = 500
      frame.joined.onEvent({ type: "synced", seq: frame.data.seq })
    })
    source.addEventListener("presence", (message) => {
      const frame = parse<RoomFrame & { client: string; presence: string }>(
        message
      )
      if (!frame) return
      frame.joined.onEvent({
        type: "presence",
        client: frame.data.client,
        presence: fromBase64(frame.data.presence),
      })
    })
    source.onerror = () => {
      source.close()
      if (this.source !== source) return
      this.source = null
      this.schedule(this.backoff)
      this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF_MS)
    }
  }
}
