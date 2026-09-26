// Open SWE names MCP tools `mcp_<connection>_<tool>_<10-hex hash>`, truncated before the hash.
export function parseToolName(runName: string): { connection: string; tool: string } {
  const body = runName.replace(/^mcp_/, '').replace(/_[0-9a-f]{10}$/, '');
  const split = body.indexOf('_');
  if (split <= 0) return { connection: body, tool: body };
  return { connection: body.slice(0, split), tool: body.slice(split + 1) };
}

export function formatCount(value: number): string {
  return value.toLocaleString('en-US');
}
