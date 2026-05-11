import { slashCommands } from "@app/commands.js";
import type { SlashCommand } from "@types";

export type ParsedSlashCommand = {
  commandName: string;
  args: string;
};

export type SlashCommandResolution =
  | { kind: "command"; command: SlashCommand; args: string }
  | { kind: "prompt" };

/**
 * Parse a leading slash command into the command token and the remaining args.
 * Mirrors the reference parser, but leaves unknown command handling to the
 * resolver so slash-prefixed prompt text can still be sent to the model.
 */
export function parseSlashCommand(input: string): ParsedSlashCommand | null {
  const trimmedInput = input.trim();

  if (!trimmedInput.startsWith("/")) {
    return null;
  }

  const withoutSlash = trimmedInput.slice(1);
  const words = withoutSlash.split(" ");

  if (!words[0]) {
    return null;
  }

  return {
    commandName: words[0],
    args: words.slice(1).join(" "),
  };
}

export function findSlashCommand(
  commandName: string,
  commands: readonly SlashCommand[] = slashCommands,
): SlashCommand | null {
  const normalized = commandName.toLowerCase();
  return (
    commands.find(
      (command) =>
        command.name === normalized ||
        command.aliases?.some((alias) => alias.toLowerCase() === normalized),
    ) ?? null
  );
}

export function resolveSlashCommand(
  input: string,
  commands: readonly SlashCommand[] = slashCommands,
): SlashCommandResolution {
  const parsed = parseSlashCommand(input);
  if (!parsed) {
    return { kind: "prompt" };
  }

  const command = findSlashCommand(parsed.commandName, commands);
  if (!command) {
    return { kind: "prompt" };
  }

  return { kind: "command", command, args: parsed.args };
}

export function getSlashCommandSuggestions(
  input: string,
  commands: readonly SlashCommand[] = slashCommands,
): SlashCommand[] {
  if (!input.startsWith("/")) {
    return [];
  }

  const commandText = input.slice(1);
  if (commandText.includes(" ")) {
    return [];
  }

  const query = commandText.toLowerCase();
  if (!query) {
    return [...commands];
  }

  return commands.filter(
    (command) =>
      command.name.startsWith(query) ||
      command.aliases?.some((alias) => alias.toLowerCase().startsWith(query)),
  );
}
