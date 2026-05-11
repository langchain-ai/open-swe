import { Box, Static } from "ink";
import { Welcome } from "./components/Welcome.js";
import { Message } from "./components/Message.js";
import { PromptInput } from "./components/PromptInput.js";
import { StatusLine } from "./components/StatusLine.js";
import { BusyLine } from "./components/BusyLine.js";
import { useAppState } from "./hooks/useAppState.js";

export const App = () => {
  const appState = useAppState();

  // Split messages: all messages except the last one go in <Static> scrollback
  // (they never re-render, which is what we want for tool results, etc.).
  // The most recent message stays "live" so streaming updates still mutate.
  const messages = appState.messages;
  const staticMessages = messages.length > 1 ? messages.slice(0, -1) : [];
  const liveMessage =
    messages.length > 0 ? messages[messages.length - 1] : null;

  return (
    <Box flexDirection="column">
      <Static
        items={[
          { kind: "welcome" as const, key: "welcome" },
          ...staticMessages.map((message) => ({
            kind: "message" as const,
            key: message.id,
            message,
          })),
        ]}
      >
        {(item) => {
          if (item.kind === "welcome") {
            return (
              <Welcome key="welcome" modelConfig={appState.currentModel} />
            );
          }
          return <Message key={item.key} message={item.message} />;
        }}
      </Static>

      {liveMessage ? <Message message={liveMessage} /> : null}

      {appState.busy ? <BusyLine label={appState.busyText} /> : null}

      <Box marginTop={1} flexDirection="column">
        <PromptInput
          query={appState.query}
          onChange={appState.onChange}
          onSubmit={appState.onSubmit}
          cursorOffset={appState.cursorOffset}
          onChangeCursorOffset={appState.onChangeCursorOffset}
          onPaste={appState.onPaste}
          onImagePaste={appState.onImagePaste}
          onExit={appState.onExit}
          inputFilter={appState.inputFilter}
          columns={appState.cols}
          mode={appState.mode}
          showCommandMenu={appState.showCommandMenu}
          filteredCommands={appState.filteredCommands}
          commandSelectionIndex={appState.commandSelectionIndex}
          showFileSearchMenu={appState.showFileSearchMenu}
          fileSearchMatches={appState.fileSearchMatches}
          fileSearchSelectionIndex={appState.fileSearchSelectionIndex}
          showModelMenu={appState.showModelMenu}
          filteredModels={appState.filteredModels}
          modelSelectionIndex={appState.modelSelectionIndex}
          currentModelId={appState.currentModelId}
          showApiKeysMenu={appState.showApiKeysMenu}
          apiKeyItems={appState.apiKeyItems}
          apiKeysSelectionIndex={appState.apiKeysSelectionIndex}
        />
        <StatusLine />
      </Box>
    </Box>
  );
};
