const { execFileSync } = require("node:child_process");
const { randomBytes } = require("node:crypto");

let shellEnv: Record<string, string | undefined> | null = null;

function shellEnvFromOutput(
  result: string,
  mark: string,
  baseEnv: Record<string, string | undefined>,
) {
  const start = result.indexOf(mark);
  const end = result.lastIndexOf(mark);
  if (start === -1 || start === end) return null;
  const resolved = { ...baseEnv };
  for (const line of result.slice(start + mark.length, end).split("\n")) {
    const separator = line.indexOf("=");
    if (separator > 0)
      resolved[line.slice(0, separator)] = line.slice(separator + 1);
  }
  return resolved;
}

/**
 * The environment of the person's login shell, which an app launched from the
 * Dock does not inherit, so approved local commands find the tools they would
 * in a terminal. Read once and kept.
 */
function getUserShellEnv(): Record<string, string | undefined> {
  if (shellEnv) return shellEnv;
  const shell = process.env.SHELL || "/bin/zsh";
  for (const args of [
    ["-il", "-c"],
    ["-l", "-c"],
    ["-i", "-c"],
  ]) {
    try {
      const mark = randomBytes(8).toString("hex");
      const result = execFileSync(
        shell,
        [...args, `echo '${mark}'; env; echo '${mark}'`],
        {
          encoding: "utf8",
          timeout: 10_000,
          stdio: ["pipe", "pipe", "pipe"],
        },
      );
      const resolved = shellEnvFromOutput(result, mark, process.env);
      if (!resolved) continue;
      shellEnv = resolved;
      return shellEnv;
    } catch {}
  }
  shellEnv = { ...process.env };
  return shellEnv;
}

module.exports = { getUserShellEnv };
