export function wrapScript(command: string): string {
  return `script --return --quiet -c "$(cat <<'OPEN_SWE_X'
${command}
OPEN_SWE_X
)" /dev/null`;
}
