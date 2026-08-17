import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"

import { desktopAcpMessages } from "./desktopAcpMessages"
import type {
  DesktopAcpDiff,
  DesktopAcpEvent,
  DesktopAcpSession,
  DesktopAcpSessionSummary,
} from "@/desktop"

const NO_DIFF: DesktopAcpDiff = {
  status: "missing",
  truncated: false,
  files: [],
}

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

function mergeEvent(
  session: DesktopAcpSession,
  event: DesktopAcpEvent
): DesktopAcpSession {
  const status =
    event.type === "run-start"
      ? "running"
      : event.type === "run-end"
        ? "idle"
        : event.type === "error"
          ? "error"
          : session.status
  return mergeSession(session, { ...session, status, events: [event] })
}

export function useDesktopAcpSession(sessionId: string) {
  const [session, setSession] = useState<DesktopAcpSession | null>(null)
  const [loadedSessionId, setLoadedSessionId] = useState<string | null>(null)

  useEffect(() => {
    setSession(null)
    setLoadedSessionId(null)
    const desktop = window.openSweDesktop
    if (!desktop) {
      setLoadedSessionId(sessionId)
      return
    }
    let active = true
    const pendingEvents: Array<DesktopAcpEvent> = []
    const unsubscribe = desktop.onAcpEvent((payload) => {
      if (payload.sessionId !== sessionId) return
      pendingEvents.push(payload.event)
      setSession((current) => {
        if (!current || current.id !== sessionId) return current
        return mergeEvent(current, payload.event)
      })
    })
    void desktop.getAcpSession(sessionId).then((next) => {
      if (!active) return
      if (next) {
        const hydrated = pendingEvents.reduce(mergeEvent, next)
        setSession((current) =>
          current?.id === sessionId ? mergeSession(hydrated, current) : hydrated
        )
      }
      setLoadedSessionId(sessionId)
    })
    return () => {
      active = false
      unsubscribe()
    }
  }, [sessionId])

  const currentSession = session?.id === sessionId ? session : null

  const messages = useMemo(
    () => desktopAcpMessages(currentSession?.events ?? []),
    [currentSession?.events]
  )
  return {
    session: currentSession,
    messages,
    loaded: loadedSessionId === sessionId,
  }
}

/**
 * What a local session changed, read from git in the desktop main process. The
 * agent edits the real project, so the panel polls while a run is live and
 * settles with one more read once it finishes.
 */
export function useLocalSessionDiff(
  sessionId: string,
  enabled: boolean,
  isRunning: boolean
) {
  const query = useQuery({
    queryKey: ["local-session-diff", sessionId],
    queryFn: () => window.openSweDesktop?.getAcpDiff(sessionId) ?? NO_DIFF,
    enabled,
    refetchInterval: isRunning ? 5000 : false,
  })

  const { refetch } = query
  useEffect(() => {
    if (enabled && !isRunning) void refetch()
  }, [enabled, isRunning, refetch])

  return query
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
  const deletedSessionIds = useRef(new Set<string>())

  useEffect(() => {
    const desktop = window.openSweDesktop
    if (!desktop) return
    const unsubscribe = desktop.onAcpEvent(({ session }) => {
      if (deletedSessionIds.current.has(session.id)) return
      setSessions((current) => mergeSummaries(current, [session]))
    })
    void desktop.listAcpSessions().then((incoming) => {
      setSessions((current) =>
        mergeSummaries(
          current,
          incoming.filter(
            (session) => !deletedSessionIds.current.has(session.id)
          )
        )
      )
    })
    return unsubscribe
  }, [])

  const deleteSession = useCallback(async (sessionId: string) => {
    const deleted =
      (await window.openSweDesktop?.deleteAcpSession(sessionId)) ?? false
    if (deleted) {
      deletedSessionIds.current.add(sessionId)
      setSessions((current) =>
        current.filter((session) => session.id !== sessionId)
      )
    }
    return deleted
  }, [])

  return { sessions, deleteSession }
}
