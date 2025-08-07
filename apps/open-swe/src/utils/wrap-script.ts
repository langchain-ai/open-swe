export function wrapScript(command: string): string {
  const makeDelim = () =>
    `OPEN_SWE_${Date.now()}_${Math.random().toString(36).slice(2)}`;

  // Ensure the delimiter does not appear as a standalone line in the command
  let delim = makeDelim();
  const containsStandalone = (d: string) =>
    command === d ||
    command.startsWith(`${d}\n`) ||
    command.endsWith(`\n${d}`) ||
    command.includes(`\n${d}\n`);

  while (containsStandalone(delim)) {
    delim = makeDelim();
  }

  return `script --return --quiet -c "$(cat <<'${delim}'
${command}
${delim}
)" /dev/null`;
}
