export function wrapScript(command: string): string {
  // Use bash directly to avoid script command compatibility issues
  return `bash -c "$(cat <<'OPEN_SWE_X'
${command}
OPEN_SWE_X
)"`;
}
