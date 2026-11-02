/**
 * The live rooms one plugin frame has joined. The frame cannot open a socket,
 * so the web UI holds one SSE stream for all of them and hands what it
 * carries to each room's listener. One stream, not one per room: browsers give
 * a host only a few HTTP/1.1 connections, and every open tab needs its own.
 *
 * Joining or leaving a room reopens the stream after the last update each
 * room delivered, so the frame never misses one and never sees one twice.
 * A room joined more than once keeps every listener, each from its own place
 * in the log.
 */
import type { RoomEvent } from "@openswe/plugin-sdk"

import type { RoomTarget } from "@/features/plugins/lib/api"
import { fromBase64, roomsStreamUrl } from "@/features/plugins/lib/api"
import { dashboardApiUrl } from "@/lib/dashboard-fetch"

const MAX_BACKOFF_MS = 15_000

interface Listener {
  seq: number
  onEvent: (event: RoomEvent) => void
}

interface Joined {
  target: RoomTarget
  listeners: Set<Listener>
}

/** Where the stream resumes: after the update the furthest-behind listener saw. */
function resumeAfter(joined: Joined): number {
  return Math.min(...[...joined.listeners].map((listener) => listener.seq))
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
    let joined = this.joined.get(key)
    if (!joined) {
      joined = { target, listeners: new Set() }
      this.joined.set(key, joined)
    }
    const room = joined
    const listener: Listener = { seq: after, onEvent }
    room.listeners.add(listener)
    this.schedule(0)
    return () => {
      if (!room.listeners.delete(listener)) return
      if (room.listeners.size === 0 && this.joined.get(key) === room)
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
          [...this.joined.values()].map((joined) => ({
            ...joined.target,
            after: resumeAfter(joined),
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
      const update = fromBase64(frame.data.update)
      for (const listener of frame.joined.listeners) {
        if (frame.data.seq <= listener.seq) continue
        listener.seq = frame.data.seq
        listener.onEvent({ type: "update", seq: frame.data.seq, update })
      }
    })
    source.addEventListener("synced", (message) => {
      const frame = parse<RoomFrame & { seq: number }>(message)
      if (!frame) return
      this.backoff = 500
      for (const listener of frame.joined.listeners) {
        listener.seq = Math.max(listener.seq, frame.data.seq)
        listener.onEvent({ type: "synced", seq: frame.data.seq })
      }
    })
    source.addEventListener("presence", (message) => {
      const frame = parse<RoomFrame & { client: string; presence: string }>(
        message
      )
      if (!frame) return
      const presence = fromBase64(frame.data.presence)
      for (const listener of frame.joined.listeners)
        listener.onEvent({
          type: "presence",
          client: frame.data.client,
          presence,
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
