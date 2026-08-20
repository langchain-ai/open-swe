import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import type { DesktopTerminalBridge, DesktopTerminalSummary } from "@/desktop"
import type { TerminalTarget } from "@/features/agents/lib/terminalSession"
import type {
  TerminalSplitDirection,
  TerminalUiState,
} from "@/features/agents/lib/terminalState"
import {
  addTerminalGroup,
  closeTerminal,
  focusTerminal,
  readTerminalState,
  reconcileTerminalIds,
  splitTerminal,
  writeTerminalState,
} from "@/features/agents/lib/terminalState"
import { useDesktopTerminalMetadata } from "@/features/agents/lib/terminalSession"

export interface TerminalGroupsController {
  state: TerminalUiState
  metadataById: Map<string, DesktopTerminalSummary>
  error: string | null
  /** Creates a terminal group and returns its id so a tab can be opened for it. */
  addGroup: () => string
  closeGroup: (groupId: string) => Promise<boolean>
  closeTerminal: (terminalId: string) => Promise<boolean>
  focus: (terminalId: string) => void
  split: (direction: TerminalSplitDirection) => void
  clear: (terminalId: string) => void
  restart: (terminalId: string) => void
  clearRequests: ReadonlyMap<string, number>
  restartRequests: ReadonlyMap<string, number>
}

export function useTerminalGroups(
  target: TerminalTarget,
  cwd: string
): TerminalGroupsController {
  const localSessionId =
    target.kind === "local" ? target.sessionId : target.threadId
  const [state, setState] = useState<TerminalUiState>(() =>
    readTerminalState(localSessionId)
  )
  const [error, setError] = useState<string | null>(null)
  const [clearRequests, setClearRequests] = useState<
    ReadonlyMap<string, number>
  >(new Map())
  const [restartRequests, setRestartRequests] = useState<
    ReadonlyMap<string, number>
  >(new Map())
  const metadata = useDesktopTerminalMetadata(
    localSessionId,
    target.kind === "local"
  )
  const stateRef = useRef(state)
  stateRef.current = state

  const commit = useCallback(
    (next: TerminalUiState) => {
      stateRef.current = next
      writeTerminalState(localSessionId, next)
      setState(next)
      return next
    },
    [localSessionId]
  )

  useEffect(() => {
    setError(null)
    commit(readTerminalState(localSessionId))
  }, [commit, localSessionId])

  useEffect(() => {
    const next = reconcileTerminalIds(
      stateRef.current,
      metadata.map((terminal) => terminal.terminalId)
    )
    if (next.terminalIds.length !== stateRef.current.terminalIds.length) {
      commit(next)
    }
  }, [commit, metadata])

  const metadataById = useMemo(
    () => new Map(metadata.map((terminal) => [terminal.terminalId, terminal])),
    [metadata]
  )

  const run = useCallback(
    async (
      fallback: string,
      action: (bridge: DesktopTerminalBridge) => Promise<void>
    ): Promise<boolean> => {
      const bridge = window.openSweDesktop?.terminal
      if (!bridge) return false
      setError(null)
      try {
        await action(bridge)
        return true
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : fallback)
        return false
      }
    },
    []
  )

  const closeTerminals = useCallback(
    (terminalIds: ReadonlyArray<string>) => {
      if (target.kind === "cloud") {
        commit(terminalIds.reduce(closeTerminal, stateRef.current))
        return Promise.resolve(true)
      }
      return run("Unable to close terminal", async (bridge) => {
        for (const terminalId of terminalIds) {
          await bridge.close({
            localSessionId,
            terminalId,
            deleteHistory: true,
          })
        }
        commit(terminalIds.reduce(closeTerminal, stateRef.current))
      })
    },
    [commit, localSessionId, run, target.kind]
  )

  return useMemo(
    () => ({
      state,
      metadataById,
      error,
      addGroup: () =>
        commit(addTerminalGroup(stateRef.current)).activeTerminalGroupId,
      closeGroup: (groupId: string) =>
        closeTerminals(
          stateRef.current.terminalGroups.find((group) => group.id === groupId)
            ?.terminalIds ?? []
        ),
      closeTerminal: (terminalId: string) => closeTerminals([terminalId]),
      focus: (terminalId: string) =>
        commit(focusTerminal(stateRef.current, terminalId)),
      split: (direction: TerminalSplitDirection) =>
        commit(splitTerminal(stateRef.current, direction)),
      clear: (terminalId: string) => {
        if (target.kind === "cloud") {
          setClearRequests((current) => {
            const next = new Map(current)
            next.set(terminalId, (next.get(terminalId) ?? 0) + 1)
            return next
          })
          return
        }
        void run("Unable to clear terminal", (bridge) =>
          bridge.clear({ localSessionId, terminalId })
        )
      },
      restart: (terminalId: string) => {
        if (target.kind === "cloud") {
          setRestartRequests((current) => {
            const next = new Map(current)
            next.set(terminalId, (next.get(terminalId) ?? 0) + 1)
            return next
          })
          return
        }
        void run("Unable to restart terminal", async (bridge) => {
          await bridge.restart({
            localSessionId,
            terminalId,
            cwd: metadataById.get(terminalId)?.cwd ?? cwd,
          })
        })
      },
      clearRequests,
      restartRequests,
    }),
    [
      clearRequests,
      closeTerminals,
      commit,
      cwd,
      error,
      localSessionId,
      metadataById,
      restartRequests,
      run,
      state,
      target.kind,
    ]
  )
}
