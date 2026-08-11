export const MAX_TERMINALS_PER_GROUP = 4
export const DEFAULT_TERMINAL_ID = "term-1"

export type TerminalSplitDirection = "horizontal" | "vertical"

export interface TerminalGroup {
  id: string
  terminalIds: Array<string>
  splitDirection?: TerminalSplitDirection
}

export interface TerminalUiState {
  terminalIds: Array<string>
  activeTerminalId: string
  terminalGroups: Array<TerminalGroup>
  activeTerminalGroupId: string
  nextTerminalNumber: number
}

const STORAGE_PREFIX = "open-swe.local-terminal.v1:"

function groupId(terminalId: string): string {
  return `group-${terminalId}`
}

function terminalNumber(terminalId: string): number {
  const match = /^term-(\d+)$/.exec(terminalId)
  return match ? Number(match[1]) : 0
}

function uniqueIds(ids: ReadonlyArray<string>): Array<string> {
  return [...new Set(ids.map((id) => id.trim()).filter(Boolean))]
}

export function normalizeTerminalState(
  input: TerminalUiState
): TerminalUiState {
  const terminalIds = uniqueIds(input.terminalIds)
  const validIds = new Set(terminalIds)
  const assigned = new Set<string>()
  const usedGroupIds = new Set<string>()
  const terminalGroups: Array<TerminalGroup> = []

  for (const candidate of input.terminalGroups) {
    const ids = uniqueIds(candidate.terminalIds).filter(
      (id) => validIds.has(id) && !assigned.has(id)
    )
    if (ids.length === 0) continue
    ids.forEach((id) => assigned.add(id))
    const base = candidate.id.trim() || groupId(ids[0]!)
    let id = base
    let suffix = 2
    while (usedGroupIds.has(id)) id = `${base}-${suffix++}`
    usedGroupIds.add(id)
    terminalGroups.push({
      id,
      terminalIds: ids,
      ...(candidate.splitDirection === "vertical"
        ? { splitDirection: "vertical" as const }
        : {}),
    })
  }

  for (const id of terminalIds) {
    if (assigned.has(id)) continue
    let nextGroupId = groupId(id)
    let suffix = 2
    while (usedGroupIds.has(nextGroupId)) {
      nextGroupId = `${groupId(id)}-${suffix++}`
    }
    usedGroupIds.add(nextGroupId)
    terminalGroups.push({ id: nextGroupId, terminalIds: [id] })
  }

  const activeTerminalId = terminalIds.includes(input.activeTerminalId)
    ? input.activeTerminalId
    : (terminalIds[0] ?? "")
  const activeTerminalGroupId =
    terminalGroups.find((group) => group.id === input.activeTerminalGroupId)
      ?.id ??
    terminalGroups.find((group) => group.terminalIds.includes(activeTerminalId))
      ?.id ??
    terminalGroups[0]?.id ??
    ""
  const highestNumber = Math.max(0, ...terminalIds.map(terminalNumber))

  return {
    terminalIds,
    activeTerminalId,
    terminalGroups,
    activeTerminalGroupId,
    nextTerminalNumber: Math.max(
      highestNumber + 1,
      Number.isInteger(input.nextTerminalNumber) ? input.nextTerminalNumber : 1,
      1
    ),
  }
}

export function createTerminalState(): TerminalUiState {
  return {
    terminalIds: [DEFAULT_TERMINAL_ID],
    activeTerminalId: DEFAULT_TERMINAL_ID,
    terminalGroups: [
      { id: groupId(DEFAULT_TERMINAL_ID), terminalIds: [DEFAULT_TERMINAL_ID] },
    ],
    activeTerminalGroupId: groupId(DEFAULT_TERMINAL_ID),
    nextTerminalNumber: 2,
  }
}

export function readTerminalState(sessionId: string): TerminalUiState {
  if (typeof window === "undefined") return createTerminalState()
  const raw = window.localStorage.getItem(`${STORAGE_PREFIX}${sessionId}`)
  if (!raw) return createTerminalState()
  try {
    const parsed = JSON.parse(raw) as Partial<TerminalUiState>
    return normalizeTerminalState({
      terminalIds: Array.isArray(parsed.terminalIds) ? parsed.terminalIds : [],
      activeTerminalId:
        typeof parsed.activeTerminalId === "string"
          ? parsed.activeTerminalId
          : "",
      terminalGroups: Array.isArray(parsed.terminalGroups)
        ? parsed.terminalGroups
        : [],
      activeTerminalGroupId:
        typeof parsed.activeTerminalGroupId === "string"
          ? parsed.activeTerminalGroupId
          : "",
      nextTerminalNumber:
        typeof parsed.nextTerminalNumber === "number"
          ? parsed.nextTerminalNumber
          : 1,
    })
  } catch {
    return createTerminalState()
  }
}

export function writeTerminalState(
  sessionId: string,
  state: TerminalUiState
): void {
  if (typeof window === "undefined") return
  window.localStorage.setItem(
    `${STORAGE_PREFIX}${sessionId}`,
    JSON.stringify(normalizeTerminalState(state))
  )
}

function allocateTerminal(state: TerminalUiState): [string, number] {
  let number = state.nextTerminalNumber
  let id = `term-${number}`
  while (state.terminalIds.includes(id)) id = `term-${++number}`
  return [id, number + 1]
}

export function addTerminalGroup(state: TerminalUiState): TerminalUiState {
  const normalized = normalizeTerminalState(state)
  const [terminalId, nextTerminalNumber] = allocateTerminal(normalized)
  const id = groupId(terminalId)
  return normalizeTerminalState({
    ...normalized,
    terminalIds: [...normalized.terminalIds, terminalId],
    activeTerminalId: terminalId,
    terminalGroups: [
      ...normalized.terminalGroups,
      { id, terminalIds: [terminalId] },
    ],
    activeTerminalGroupId: id,
    nextTerminalNumber,
  })
}

export function splitTerminal(
  state: TerminalUiState,
  splitDirection: TerminalSplitDirection
): TerminalUiState {
  const normalized = normalizeTerminalState(state)
  if (normalized.terminalIds.length === 0) return addTerminalGroup(normalized)
  const groupIndex = normalized.terminalGroups.findIndex(
    (group) => group.id === normalized.activeTerminalGroupId
  )
  const group = normalized.terminalGroups[groupIndex]
  if (!group || group.terminalIds.length >= MAX_TERMINALS_PER_GROUP) {
    return normalized
  }
  const [terminalId, nextTerminalNumber] = allocateTerminal(normalized)
  const anchorIndex = group.terminalIds.indexOf(normalized.activeTerminalId)
  const terminalIds = [...group.terminalIds]
  terminalIds.splice(
    anchorIndex < 0 ? terminalIds.length : anchorIndex + 1,
    0,
    terminalId
  )
  const terminalGroups = normalized.terminalGroups.map((candidate, index) =>
    index === groupIndex
      ? {
          ...candidate,
          terminalIds,
          ...(splitDirection === "vertical"
            ? { splitDirection: "vertical" as const }
            : { splitDirection: undefined }),
        }
      : candidate
  )
  return normalizeTerminalState({
    ...normalized,
    terminalIds: [...normalized.terminalIds, terminalId],
    activeTerminalId: terminalId,
    terminalGroups,
    nextTerminalNumber,
  })
}

export function focusTerminal(
  state: TerminalUiState,
  terminalId: string
): TerminalUiState {
  const normalized = normalizeTerminalState(state)
  if (!normalized.terminalIds.includes(terminalId)) return normalized
  return {
    ...normalized,
    activeTerminalId: terminalId,
    activeTerminalGroupId:
      normalized.terminalGroups.find((group) =>
        group.terminalIds.includes(terminalId)
      )?.id ?? normalized.activeTerminalGroupId,
  }
}

export function closeTerminal(
  state: TerminalUiState,
  terminalId: string
): TerminalUiState {
  const normalized = normalizeTerminalState(state)
  const closedIndex = normalized.terminalIds.indexOf(terminalId)
  if (closedIndex < 0) return normalized
  const terminalIds = normalized.terminalIds.filter((id) => id !== terminalId)
  const activeTerminalId =
    normalized.activeTerminalId === terminalId
      ? (terminalIds[Math.min(closedIndex, terminalIds.length - 1)] ?? "")
      : normalized.activeTerminalId
  return normalizeTerminalState({
    ...normalized,
    terminalIds,
    activeTerminalId,
    terminalGroups: normalized.terminalGroups
      .map((group) => ({
        ...group,
        terminalIds: group.terminalIds.filter((id) => id !== terminalId),
      }))
      .filter((group) => group.terminalIds.length > 0),
  })
}

export function reconcileTerminalIds(
  state: TerminalUiState,
  serverIds: ReadonlyArray<string>
): TerminalUiState {
  const normalized = normalizeTerminalState(state)
  const terminalIds = uniqueIds([...normalized.terminalIds, ...serverIds])
  return normalizeTerminalState({ ...normalized, terminalIds })
}
