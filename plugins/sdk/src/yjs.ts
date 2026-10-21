/**
 * A Yjs provider over an Open SWE live room, for plugins that edit together.
 *
 * Plugins have no network, so instead of a WebSocket the provider talks to the
 * dashboard, which keeps the room's log in PostgreSQL and fans updates out to
 * everyone in it. Local edits are batched into one update per `flushMs`;
 * awareness (cursors, names) travels as presence and is never stored.
 */
import {
  Awareness,
  applyAwarenessUpdate,
  encodeAwarenessUpdate,
  removeAwarenessStates,
} from "y-protocols/awareness"
import * as Y from "yjs"

import type { LiveRoom, PluginHost } from "./client"
import type { RoomScope } from "./protocol"

export interface RoomProviderOptions {
  /** How long local edits gather before they are sent as one update. */
  flushMs?: number
  /** After this many updates, a member replaces the log with one snapshot. */
  compactAfter?: number
}

const AWARENESS_HEARTBEAT_MS = 15_000

export class RoomProvider {
  readonly awareness: Awareness
  private readonly room: LiveRoom
  private readonly flushMs: number
  private readonly compactAfter: number
  private pending: Uint8Array[] = []
  private flushTimer: ReturnType<typeof setTimeout> | null = null
  private heartbeat: ReturnType<typeof setInterval>
  private sinceCompaction = 0
  private syncedOnce = false
  private readonly syncWaiters = new Set<() => void>()

  constructor(
    host: PluginHost,
    scope: RoomScope,
    readonly doc: Y.Doc,
    options: RoomProviderOptions = {}
  ) {
    this.flushMs = options.flushMs ?? 50
    this.compactAfter = options.compactAfter ?? 200
    this.awareness = new Awareness(doc)
    this.room = host.joinRoom(scope, {
      onUpdate: (update) => {
        this.sinceCompaction++
        Y.applyUpdate(doc, update, this)
      },
      onSynced: () => {
        if (!this.syncedOnce) {
          this.syncedOnce = true
          for (const resolve of this.syncWaiters) resolve()
          this.syncWaiters.clear()
          this.announce([doc.clientID])
        }
        this.maybeCompact()
      },
      onPresence: (_client, presence) =>
        applyAwarenessUpdate(this.awareness, presence, this),
    })
    doc.on("update", this.onDocUpdate)
    this.awareness.on("update", this.onAwarenessUpdate)
    this.heartbeat = setInterval(
      () => this.announce([doc.clientID]),
      AWARENESS_HEARTBEAT_MS
    )
  }

  get synced(): boolean {
    return this.syncedOnce
  }

  /** Resolves once the room's stored log has been applied to the document. */
  whenSynced(): Promise<void> {
    if (this.syncedOnce) return Promise.resolve()
    return new Promise((resolve) => this.syncWaiters.add(resolve))
  }

  private readonly onDocUpdate = (update: Uint8Array, origin: unknown) => {
    if (origin === this) return
    this.pending.push(update)
    this.flushTimer ??= setTimeout(this.flush, this.flushMs)
  }

  private readonly flush = () => {
    this.flushTimer = null
    if (!this.pending.length) return
    const update = Y.mergeUpdates(this.pending)
    this.pending = []
    void this.room.publish(update)
  }

  private readonly onAwarenessUpdate = (
    changes: { added: number[]; updated: number[]; removed: number[] },
    origin: unknown
  ) => {
    if (origin === this) return
    this.announce([...changes.added, ...changes.updated, ...changes.removed])
  }

  private announce(clients: number[]): void {
    void this.room.presence(
      String(this.doc.clientID),
      encodeAwarenessUpdate(this.awareness, clients)
    )
  }

  private maybeCompact(): void {
    if (this.sinceCompaction < this.compactAfter) return
    this.sinceCompaction = 0
    void this.room.compact(Y.encodeStateAsUpdate(this.doc), this.room.seq)
  }

  destroy(): void {
    this.flush()
    clearInterval(this.heartbeat)
    removeAwarenessStates(this.awareness, [this.doc.clientID], "leave")
    this.announce([this.doc.clientID])
    this.doc.off("update", this.onDocUpdate)
    this.awareness.off("update", this.onAwarenessUpdate)
    this.awareness.destroy()
    this.room.leave()
  }
}
