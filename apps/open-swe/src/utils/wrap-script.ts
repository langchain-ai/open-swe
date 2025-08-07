import { v4 as uuidv4 } from "uuid";

export function wrapScript(command: string): string {
  const makeDelim = () => `OPEN_SWE_${uuidv4()}`;

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
