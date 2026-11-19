import { useCallback, useEffect, useMemo, useRef, useState } from "react"

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
  splitTerminal,
  writeTerminalState,
} from "@/features/agents/lib/terminalState"

export interface TerminalGroupsController {
  state: TerminalUiState
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
  target: TerminalTarget
): TerminalGroupsController {
  const { threadId } = target
  const [state, setState] = useState<TerminalUiState>(() =>
    readTerminalState(threadId)
  )
  const [clearRequests, setClearRequests] = useState<
    ReadonlyMap<string, number>
  >(new Map())
  const [restartRequests, setRestartRequests] = useState<
    ReadonlyMap<string, number>
  >(new Map())
  const stateRef = useRef(state)
  useEffect(() => {
    stateRef.current = state
  }, [state])

  const commit = useCallback(
    (next: TerminalUiState) => {
      stateRef.current = next
      writeTerminalState(threadId, next)
      setState(next)
      return next
    },
    [threadId]
  )

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    commit(readTerminalState(threadId))
  }, [commit, threadId])

  const closeTerminals = useCallback(
    (terminalIds: ReadonlyArray<string>) => {
      commit(terminalIds.reduce(closeTerminal, stateRef.current))
      return Promise.resolve(true)
    },
    [commit]
  )

  return useMemo(
    () => ({
      state,
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
      clear: (terminalId: string) =>
        setClearRequests((current) =>
          new Map(current).set(terminalId, (current.get(terminalId) ?? 0) + 1)
        ),
      restart: (terminalId: string) =>
        setRestartRequests((current) =>
          new Map(current).set(terminalId, (current.get(terminalId) ?? 0) + 1)
        ),
      clearRequests,
      restartRequests,
    }),
    [clearRequests, closeTerminals, commit, restartRequests, state]
  )
}
