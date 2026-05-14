import {
  DiffLine,
  SlashCommand,
  StructuredPatchHunk,
} from "@types";

export type DiffRowProps = {
  line: DiffLine;
  pad: number;
};

export type DiffViewProps = {
  diffLines?: DiffLine[];
  hunks?: StructuredPatchHunk[];
  filePath?: string;
  width?: number;
};

export type CommandMenuProps = {
  commands: SlashCommand[];
  selectedIndex: number;
};

export type CodeBlockProps = {
  lines: string[];
};
