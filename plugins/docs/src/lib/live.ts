/**
 * Shared, live state through the dashboard's rooms: a draft's document, edited
 * by everyone at once, and each page's list of who is looking at it.
 *
 * Each room is opened once however many components read it, and closed when
 * the last one unmounts; components read it through `useSyncExternalStore`.
 */
import type { PluginHost } from "@openswe/plugin-sdk"
import { usePluginHost } from "@openswe/plugin-sdk/react"
import { RoomProvider } from "@openswe/plugin-sdk/yjs"
import type { Editor } from "@tiptap/core"
import {
  prosemirrorJSONToYXmlFragment,
  yXmlFragmentToProsemirrorJSON,
} from "@tiptap/y-tiptap"
import { useCallback, useEffect, useSyncExternalStore } from "react"
import * as Y from "yjs"

import { colorFor, presenceRoom } from "@/lib/model"

export const FIELD = "default"

interface Session<S> {
  snapshot: S
  destroy: () => void
}

/**
 * Sessions by key, opened by their first subscriber and closed a tick after
 * their last, so a remount (or StrictMode's double mount) keeps the room open.
 */
class Sessions<S> {
  private readonly entries = new Map<
    string,
    {
      session: Session<S>
      listeners: Set<() => void>
      closing: ReturnType<typeof setTimeout> | null
    }
  >()

  constructor(
    private readonly open: (
      key: string,
      changed: (snapshot: S) => void
    ) => Session<S>,
    private readonly empty: S
  ) {}

  subscribe(key: string, listener: () => void): () => void {
    let entry = this.entries.get(key)
    if (!entry) {
      const listeners = new Set<() => void>()
      const session = this.open(key, (snapshot) => {
        session.snapshot = snapshot
        for (const notify of listeners) notify()
      })
      entry = { session, listeners, closing: null }
      this.entries.set(key, entry)
    }
    if (entry.closing) clearTimeout(entry.closing)
    entry.closing = null
    entry.listeners.add(listener)
    const held = entry
    return () => {
      held.listeners.delete(listener)
      if (held.listeners.size > 0) return
      held.closing = setTimeout(() => {
        if (held.listeners.size > 0) return
        held.session.destroy()
        this.entries.delete(key)
      }, 0)
    }
  }

  snapshot(key: string): S {
    return this.entries.get(key)?.session.snapshot ?? this.empty
  }
}

function useSession<S>(sessions: Sessions<S>, key: string | null, empty: S): S {
  const subscribe = useCallback(
    (listener: () => void) =>
      key ? sessions.subscribe(key, listener) : () => {},
    [sessions, key]
  )
  return useSyncExternalStore(
    subscribe,
    () => (key ? sessions.snapshot(key) : empty),
    () => empty
  )
}

function roomKey(repository: string, room: string): string {
  return `${repository}#${room}`
}

function splitKey(key: string): { repository: string; room: string } {
  const at = key.indexOf("#")
  return { repository: key.slice(0, at), room: key.slice(at + 1) }
}

export interface LiveDoc {
  doc: Y.Doc
  provider: RoomProvider
}

type LiveState = { live: LiveDoc | null; synced: boolean }
const NO_DOC: LiveState = { live: null, synced: false }

const docSessions = new WeakMap<PluginHost, Sessions<LiveState>>()

function docsFor(host: PluginHost): Sessions<LiveState> {
  let sessions = docSessions.get(host)
  if (!sessions) {
    sessions = new Sessions<LiveState>((key, changed) => {
      const doc = new Y.Doc()
      const provider = new RoomProvider(host, splitKey(key), doc)
      const live = { doc, provider }
      let open = true
      void provider
        .whenSynced()
        .then(() => open && changed({ live, synced: true }))
      return {
        snapshot: { live, synced: false },
        destroy: () => {
          open = false
          provider.destroy()
          doc.destroy()
        },
      }
    }, NO_DOC)
    docSessions.set(host, sessions)
  }
  return sessions
}

/** A Yjs document shared through `room`, synced with what the room has stored. */
export function useLiveDoc(repository: string, room: string | null): LiveState {
  const host = usePluginHost()
  return useSession(
    docsFor(host),
    room ? roomKey(repository, room) : null,
    NO_DOC
  )
}

/**
 * Fills an empty draft document from the page's Markdown. Every member seeds
 * under client id 0, so two who arrive at once write identical items, which
 * Yjs merges into one copy instead of two.
 */
export function seedFromMarkdown(
  doc: Y.Doc,
  editor: Editor,
  markdown: string
): void {
  if (doc.getXmlFragment(FIELD).length > 0 || !editor.markdown) return
  const seed = new Y.Doc()
  seed.clientID = 0
  prosemirrorJSONToYXmlFragment(
    editor.schema,
    editor.markdown.parse(markdown),
    seed.getXmlFragment(FIELD)
  )
  Y.applyUpdate(doc, Y.encodeStateAsUpdate(seed))
  seed.destroy()
}

/**
 * `markdown` as a live draft would serialise it: through Yjs and back, the path
 * the shared document takes, so an untouched draft compares equal to its save.
 */
export function asLiveMarkdown(editor: Editor, markdown: string): string {
  if (!editor.markdown) return markdown
  const scratch = new Y.Doc()
  const fragment = scratch.getXmlFragment(FIELD)
  prosemirrorJSONToYXmlFragment(
    editor.schema,
    editor.markdown.parse(markdown),
    fragment
  )
  const normalized = editor.markdown.serialize(
    yXmlFragmentToProsemirrorJSON(fragment)
  )
  scratch.destroy()
  return normalized
}

export interface Viewer {
  login: string
  color: string
  editing: boolean
}

interface PresenceState {
  viewers: Viewer[]
  provider: RoomProvider | null
}
const NO_ONE: PresenceState = { viewers: [], provider: null }

const presenceSessions = new WeakMap<PluginHost, Sessions<PresenceState>>()

function presenceFor(host: PluginHost): Sessions<PresenceState> {
  let sessions = presenceSessions.get(host)
  if (!sessions) {
    sessions = new Sessions<PresenceState>((key, changed) => {
      const doc = new Y.Doc()
      const provider = new RoomProvider(host, splitKey(key), doc)
      const read = () => {
        const byLogin = new Map<string, Viewer>()
        for (const state of provider.awareness.getStates().values()) {
          const viewer = state.viewer as Viewer | undefined
          if (!viewer?.login) continue
          const held = byLogin.get(viewer.login)
          byLogin.set(viewer.login, {
            ...viewer,
            editing: Boolean(held?.editing || viewer.editing),
          })
        }
        changed({ viewers: [...byLogin.values()], provider })
      }
      provider.awareness.on("change", read)
      return {
        snapshot: { viewers: [], provider },
        destroy: () => {
          provider.awareness.off("change", read)
          provider.destroy()
          doc.destroy()
        },
      }
    }, NO_ONE)
    presenceSessions.set(host, sessions)
  }
  return sessions
}

/** Everyone with this page open, this person included, one entry per login. */
export function usePresence(
  repository: string,
  path: string,
  editing: boolean
): Viewer[] {
  const host = usePluginHost()
  const { viewers, provider } = useSession(
    presenceFor(host),
    roomKey(repository, presenceRoom(path)),
    NO_ONE
  )
  useEffect(() => {
    const login = host.context.user.login
    provider?.awareness.setLocalStateField("viewer", {
      login,
      color: colorFor(login),
      editing,
    })
  }, [provider, editing, host])
  return viewers
}
