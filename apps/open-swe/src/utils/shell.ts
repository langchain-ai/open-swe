export function isWriteCommand(command: string[]): boolean {
  const writeCommands = [
    "cat",
    "echo",
    "printf",
    "tee",
    "cp",
    "mv",
    "ln",
    "install",
    "rsync",
  ];

  return writeCommands.includes(command[0]);
}

export async function stopSandbox(sandboxSessionId: string): Promise<string> {
  // No-op in local mode
  return sandboxSessionId;
}

export async function deleteSandbox(
  sandboxSessionId: string,
): Promise<boolean> {
  // No-op in local mode
  return true;
}

export async function getSandboxWithErrorHandling(
  sandboxSessionId: string | undefined,
): Promise<{
  sandbox: { id: string };
  codebaseTree: string | null;
  dependenciesInstalled: boolean | null;
}> {
  return {
    sandbox: { id: sandboxSessionId || "local-mock-sandbox" },
    codebaseTree: null,
    dependenciesInstalled: null,
  };
}
