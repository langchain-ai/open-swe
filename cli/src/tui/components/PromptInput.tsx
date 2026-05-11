import { Box, Text } from "ink";
import type { Key } from "ink";
import { TextInput } from "./TextInput/index.js";
import { CommandMenu } from "./CommandMenu.js";
import { FileSearchMenu } from "./FileSearchMenu.js";
import { ModelMenu } from "./ModelMenu.js";
import { ApiKeysMenu } from "./ApiKeysMenu.js";
import { themeColor } from "@tui/theme.js";
import { ARROW_RIGHT_THIN } from "@tui/figures.js";
import type { ApiKeyMenuItem, Mode, ModelOption, SlashCommand } from "@types";

type Props = {
  query: string;
  onChange: (v: string) => void;
  onSubmit: (v: string) => void | Promise<void>;
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
  columns: number;
  mode: Mode;
  // command menu
  showCommandMenu: boolean;
  filteredCommands: SlashCommand[];
  commandSelectionIndex: number;
  // file search
  showFileSearchMenu: boolean;
  fileSearchMatches: string[];
  fileSearchSelectionIndex: number;
  // model menu
  showModelMenu: boolean;
  filteredModels: ModelOption[];
  modelSelectionIndex: number;
  currentModelId: number;
  // api keys menu
  showApiKeysMenu: boolean;
  apiKeyItems: ApiKeyMenuItem[];
  apiKeysSelectionIndex: number;
};

const placeholderForMode = (
  mode: Mode,
  showModelMenu: boolean,
  showApiKeysMenu: boolean,
): string => {
  if (showApiKeysMenu) return "Manage API keys…";
  if (showModelMenu) return "Filter models by name or ID…";
  if (mode === "plan") return "Plan something with coda…";
  return "Ask coda anything…";
};

const borderColorForMode = (
  mode: Mode,
  showModelMenu: boolean,
  showApiKeysMenu: boolean,
): string => {
  if (showApiKeysMenu) return themeColor("suggestion");
  if (showModelMenu) return themeColor("suggestion");
  if (mode === "plan") return themeColor("planMode");
  return themeColor("promptBorder");
};

export const PromptInput = (props: Props) => {
  const {
    query,
    onChange,
    onSubmit,
    cursorOffset,
    onChangeCursorOffset,
    onPaste,
    onImagePaste,
    onExit,
    inputFilter,
    columns,
    mode,
    showCommandMenu,
    filteredCommands,
    commandSelectionIndex,
    showFileSearchMenu,
    fileSearchMatches,
    fileSearchSelectionIndex,
    showModelMenu,
    filteredModels,
    modelSelectionIndex,
    currentModelId,
    showApiKeysMenu,
    apiKeyItems,
    apiKeysSelectionIndex,
  } = props;

  const borderColor = borderColorForMode(mode, showModelMenu, showApiKeysMenu);
  const promptColor =
    mode === "plan" ? themeColor("planMode") : themeColor("brand");

  const anyMenuOpen =
    showCommandMenu || showFileSearchMenu || showModelMenu || showApiKeysMenu;
  // Reserve 6 cols for the prompt arrow ("> "), horizontal padding (paddingX=1
  // on both sides) and the rounded border (1 col each side) so the cursor
  // wraps at the visual width of the inner box, not the terminal width.
  const inputColumns = Math.max(8, columns - 6);

  return (
    <Box flexDirection="column">
      <Box
        borderStyle="round"
        borderColor={borderColor}
        paddingX={1}
        flexDirection="row"
        alignItems="center"
      >
        <Text color={promptColor} bold>
          {ARROW_RIGHT_THIN}{" "}
        </Text>
        <Box flexGrow={1}>
          <TextInput
            value={query}
            onChange={onChange}
            onSubmit={onSubmit}
            cursorOffset={cursorOffset}
            onChangeCursorOffset={onChangeCursorOffset}
            onPaste={onPaste}
            onImagePaste={onImagePaste}
            onExit={onExit}
            inputFilter={inputFilter}
            placeholder={placeholderForMode(mode, showModelMenu, showApiKeysMenu)}
            multiline={true}
            showCursor={true}
            focus={true}
            columns={inputColumns}
            disableCursorMovementForUpDownKeys={anyMenuOpen}
            disableEscapeDoublePress={anyMenuOpen}
          />
        </Box>
      </Box>

      {showCommandMenu && filteredCommands.length > 0 ? (
        <CommandMenu
          commands={filteredCommands}
          selectedIndex={commandSelectionIndex}
        />
      ) : null}

      {showFileSearchMenu ? (
        <FileSearchMenu
          matches={fileSearchMatches}
          selectedIndex={fileSearchSelectionIndex}
        />
      ) : null}

      {showModelMenu && filteredModels.length > 0 ? (
        <ModelMenu
          models={filteredModels}
          selectedIndex={modelSelectionIndex}
          currentModelId={currentModelId}
        />
      ) : null}

      {showApiKeysMenu ? (
        <ApiKeysMenu
          items={apiKeyItems}
          selectedIndex={apiKeysSelectionIndex}
        />
      ) : null}
    </Box>
  );
};
