import type { TerminalGroupsController } from "@/features/agents/lib/terminalGroups"

export function terminalTabTitle(
  terminals: TerminalGroupsController,
  groupId: string
): string {
  const group = terminals.state.terminalGroups.find(
    (candidate) => candidate.id === groupId
  )
  const terminalId = group?.terminalIds.includes(
    terminals.state.activeTerminalId
  )
    ? terminals.state.activeTerminalId
    : group?.terminalIds[0]
  return (
    (terminalId ? terminals.metadataById.get(terminalId)?.label : null) ||
    "Terminal"
  )
}
