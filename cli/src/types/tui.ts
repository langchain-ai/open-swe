import { slashCommands } from "@app/commands.js";
import type { Key } from "ink";
import {
  ApiKeyMenuItem,
  DiffLine,
  Message,
  Mode,
  ModelConfig,
  ModelOption,
  SlashCommand,
  StructuredPatchHunk,
} from "@types";

export type AppState = {
  // UI data
  cols: number;
  messages: Message[];
  busy: boolean;
  mode: Mode;
  currentModel: ModelConfig;
  currentModelId: number;
  busyText: string;
  // Prompt
  query: string;
  onChange: (v: string) => void;
  onSubmit: (v: string) => Promise<void>;
  setQuery: (v: string) => void;
  cursorOffset: number;
  onChangeCursorOffset: (offset: number) => void;
  onPaste: (text: string) => void;
  onImagePaste: (
    base64Image: string,
    mediaType?: string,
    filename?: string,
    sourcePath?: string,
  ) => void;
  onExit: () => void;
  inputFilter?: (input: string, key: Key) => string;
  // Menus
  showCommandMenu: boolean;
  filteredCommands: typeof slashCommands;
  commandSelectionIndex: number;
  setCommandSelectionIndex: (i: number) => void;
  showModelMenu: boolean;
  filteredModels: ModelOption[];
  modelSelectionIndex: number;
  setModelSelectionIndex: (i: number) => void;
  showFileSearchMenu: boolean;
  fileSearchMatches: string[];
  fileSearchSelectionIndex: number;
  setFileSearchSelectionIndex: (i: number) => void;
  showApiKeysMenu: boolean;
  apiKeyItems: ApiKeyMenuItem[];
  apiKeysSelectionIndex: number;
  setApiKeysSelectionIndex: (i: number) => void;
};

export type ApiKeysMenuProps = {
  items: ApiKeyMenuItem[];
  selectedIndex: number;
};

export type ModelMenuProps = {
  models: ModelOption[];
  selectedIndex: number;
  currentModelId: number;
};

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

export type FileSearchMenuProps = {
  matches: string[];
  selectedIndex: number;
};

export type CodeBlockProps = {
  lines: string[];
};
