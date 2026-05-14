import { Box, Text } from "ink";
import type { Key } from "ink";
import { TextInput } from "./TextInput/index.js";
import { CommandMenu } from "./CommandMenu.js";
import { themeColor } from "@tui/theme.js";
import { ARROW_RIGHT_THIN } from "@tui/figures.js";
import type { SlashCommand } from "@types";

type Props = {
  query: string;
  onChange: (v: string) => void;
  onSubmit: (v: string) => void | Promise<void>;
  cursorOffset: number;
  onChangeCursorOffset: (offset: number) => void;
  onPaste?: (text: string) => void;
  onExit: () => void;
  inputFilter?: (input: string, key: Key) => string;
  columns: number;
  placeholder?: string;
  // command menu (optional — Attach shows slash suggestions inline)
  showCommandMenu?: boolean;
  filteredCommands?: SlashCommand[];
  commandSelectionIndex?: number;
};

export const PromptInput = (props: Props) => {
  const {
    query,
    onChange,
    onSubmit,
    cursorOffset,
    onChangeCursorOffset,
    onPaste,
    onExit,
    inputFilter,
    columns,
    placeholder,
    showCommandMenu,
    filteredCommands,
    commandSelectionIndex,
  } = props;

  const borderColor = themeColor("promptBorder");
  const promptColor = themeColor("brand");
  const anyMenuOpen = !!showCommandMenu;
  // Reserve 6 cols for the prompt arrow, paddingX=1, and the rounded border
  // (1 col each side) so the cursor wraps at the visible inner width.
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
            onExit={onExit}
            inputFilter={inputFilter}
            placeholder={placeholder ?? "Type a message…"}
            multiline={true}
            showCursor={true}
            focus={true}
            columns={inputColumns}
            disableCursorMovementForUpDownKeys={anyMenuOpen}
            disableEscapeDoublePress={anyMenuOpen}
          />
        </Box>
      </Box>

      {showCommandMenu && filteredCommands && filteredCommands.length > 0 ? (
        <CommandMenu
          commands={filteredCommands}
          selectedIndex={commandSelectionIndex ?? 0}
        />
      ) : null}
    </Box>
  );
};
