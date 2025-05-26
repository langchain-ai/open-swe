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
