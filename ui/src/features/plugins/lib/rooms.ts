/**
 * The live rooms one plugin frame has joined. The frame cannot open a socket,
 * so each room is an SSE stream here, and what it carries is handed to the
 * frame as events. A stream that drops reconnects after the last update it
 * delivered, so the frame never misses one and never sees one twice.
 */
import type { HostEvent } from "@openswe/plugin-sdk"

import type { RoomTarget } from "@/features/plugins/lib/api"
import { fromBase64, roomStreamUrl } from "@/features/plugins/lib/api"
import { dashboardApiUrl } from "@/lib/dashboard-fetch"

const MAX_BACKOFF_MS = 15_000

function keyOf(target: RoomTarget): string {
  return `${target.repository ?? ""}#${target.room}`
}

class RoomStream {
  private source: EventSource | null = null
  private backoff = 500
  private timer: ReturnType<typeof setTimeout> | null = null
  private closed = false

  constructor(
    private readonly installationId: string,
    private readonly target: RoomTarget,
    private seq: number,
    private readonly emit: (event: HostEvent) => void
  ) {
    this.open()
  }

  private open(): void {
    const source = new EventSource(
      dashboardApiUrl(
        roomStreamUrl(this.installationId, this.target, this.seq)
      ),
      { withCredentials: true }
    )
    this.source = source
    const repository = this.target.repository ?? null
    const room = this.target.room
    source.addEventListener("update", (message) => {
      const data = JSON.parse(message.data) as { seq: number; update: string }
      this.seq = Math.max(this.seq, data.seq)
      this.emit({
        type: "room.update",
        room,
        repository,
        seq: data.seq,
        update: fromBase64(data.update),
      })
    })
    source.addEventListener("synced", (message) => {
      const data = JSON.parse(message.data) as { seq: number }
      this.backoff = 500
      this.emit({ type: "room.synced", room, repository, seq: data.seq })
    })
    source.addEventListener("presence", (message) => {
      const data = JSON.parse(message.data) as {
        client: string
        presence: string
      }
      this.emit({
        type: "room.presence",
        room,
        repository,
        client: data.client,
        presence: fromBase64(data.presence),
      })
    })
    source.onerror = () => {
      source.close()
      if (this.closed) return
      this.timer = setTimeout(() => this.open(), this.backoff)
      this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF_MS)
    }
  }

  close(): void {
    this.closed = true
    if (this.timer) clearTimeout(this.timer)
    this.source?.close()
  }
}

export class PluginRooms {
  private readonly streams = new Map<string, RoomStream>()

  constructor(
    private readonly installationId: string,
    private readonly emit: (event: HostEvent) => void
  ) {}

  join(target: RoomTarget, after: number): void {
    const key = keyOf(target)
    this.streams.get(key)?.close()
    this.streams.set(
      key,
      new RoomStream(this.installationId, target, after, this.emit)
    )
  }

  leave(target: RoomTarget): void {
    const key = keyOf(target)
    this.streams.get(key)?.close()
    this.streams.delete(key)
  }

  closeAll(): void {
    for (const stream of this.streams.values()) stream.close()
    this.streams.clear()
  }
}
