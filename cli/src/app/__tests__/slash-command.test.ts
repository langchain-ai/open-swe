import { describe, expect, it } from "vitest";
import {
  findSlashCommand,
  getSlashCommandSuggestions,
  parseSlashCommand,
  resolveSlashCommand,
} from "../slash-command.js";

describe("slash command parsing", () => {
  it("parses a slash command and arguments", () => {
    expect(parseSlashCommand("/review main")).toEqual({
      commandName: "review",
      args: "main",
    });
  });

  it("ignores non-command and bare slash input", () => {
    expect(parseSlashCommand("explain /tmp/file")).toBeNull();
    expect(parseSlashCommand("/")).toBeNull();
  });

  it("resolves aliases to their canonical command", () => {
    expect(findSlashCommand("new")?.name).toBe("clear");
    expect(resolveSlashCommand("/exit")).toMatchObject({
      kind: "command",
      command: { name: "quit" },
    });
  });

  it("treats unknown slash-prefixed text as a prompt", () => {
    expect(resolveSlashCommand("/Users/johannes/project")).toEqual({
      kind: "prompt",
    });
    expect(resolveSlashCommand("/not-a-command do work")).toEqual({
      kind: "prompt",
    });
  });

  it("suggests commands only while editing the command token", () => {
    expect(getSlashCommandSuggestions("/").length).toBeGreaterThan(0);
    expect(getSlashCommandSuggestions("/st").map((command) => command.name)).toEqual([
      "status",
    ]);
    expect(getSlashCommandSuggestions("/new").map((command) => command.name)).toEqual([
      "clear",
    ]);
    expect(getSlashCommandSuggestions("/status now")).toEqual([]);
  });
});
