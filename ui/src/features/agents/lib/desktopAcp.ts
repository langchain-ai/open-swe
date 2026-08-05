import { useEffect, useMemo, useState } from "react"

import { desktopAcpMessages } from "./desktopAcpMessages"
import type {
  DesktopAcpEvent,
  DesktopAcpSession,
  DesktopAcpSessionSummary,
} from "@/desktop"

function mergeSession(
  current: DesktopAcpSession | null,
  incoming: DesktopAcpSession
): DesktopAcpSession {
  if (!current || current.id !== incoming.id) return incoming
  const events = new Map(current.events.map((event) => [event.sequence, event]))
  for (const event of incoming.events) events.set(event.sequence, event)
  return {
    ...incoming,
    events: [...events.values()].sort(
      (left, right) => left.sequence - right.sequence
    ),
  }
}

export function useDesktopAcpSession(sessionId: string) {
  const [session, setSession] = useState<DesktopAcpSession | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    const desktop = window.openSweDesktop
    if (!desktop) return
    const pendingEvents: Array<DesktopAcpEvent> = []
    const unsubscribe = desktop.onAcpEvent((payload) => {
      if (payload.sessionId !== sessionId) return
      pendingEvents.push(payload.event)
      setSession((current) => {
        if (!current) return current
        return mergeSession(current, {
          ...current,
          status:
            payload.event.type === "run-start"
              ? "running"
              : payload.event.type === "run-end"
                ? "idle"
                : payload.event.type === "error"
                  ? "error"
                  : current.status,
          events: [payload.event],
        })
      })
    })
    void desktop.getAcpSession(sessionId).then((next) => {
      if (next) {
        const hydrated = mergeSession(next, { ...next, events: pendingEvents })
        setSession((current) => mergeSession(current, hydrated))
      }
      setLoaded(true)
    })
    return unsubscribe
  }, [sessionId])

  const messages = useMemo(
    () => desktopAcpMessages(session?.events ?? []),
    [session?.events]
  )
  return { session, messages, loaded }
}

function mergeSummaries(
  current: Array<DesktopAcpSessionSummary>,
  incoming: Array<DesktopAcpSessionSummary>
): Array<DesktopAcpSessionSummary> {
  const sessions = new Map(current.map((session) => [session.id, session]))
  for (const session of incoming) {
    const previous = sessions.get(session.id)
    if (!previous || session.updatedAt >= previous.updatedAt) {
      sessions.set(session.id, session)
    }
  }
  return [...sessions.values()].sort(
    (left, right) => right.updatedAt - left.updatedAt
  )
}

export function useDesktopAcpSessions() {
  const [sessions, setSessions] = useState<Array<DesktopAcpSessionSummary>>([])

  useEffect(() => {
    const desktop = window.openSweDesktop
    if (!desktop) return
    const unsubscribe = desktop.onAcpEvent(({ session }) => {
      setSessions((current) => mergeSummaries(current, [session]))
    })
    void desktop.listAcpSessions().then((incoming) => {
      setSessions((current) => mergeSummaries(current, incoming))
    })
    return unsubscribe
  }, [])

  return sessions
}
