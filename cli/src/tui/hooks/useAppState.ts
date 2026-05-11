import { useCallback, useRef, useState } from "react";
import type { Key } from "ink";
import { useApp, useInput } from "ink";
import { randomUUID } from "crypto";
import { BaseMessage, HumanMessage } from "@langchain/core/messages";
import {
  saveSession,
  storeModelConfig,
  storeApiKey,
  deleteStoredApiKey,
} from "@lib/storage";
import { nowTime } from "@lib/time";
import { useStore } from "@app/store.js";
import type { ModelConfig, Mode, Provider } from "@types";
import { modelOptions } from "@lib/models.js";
import { useBusyText } from "@tui/hooks/useBusyText.js";
import { useFileSearchMenu } from "@tui/hooks/useFileSearchMenu.js";
import { useCommandMenu } from "@tui/hooks/useCommandMenu.js";
import { useModelMenu } from "@tui/hooks/useModelMenu.js";
import { useApiKeysMenu } from "@tui/hooks/useApiKeysMenu.js";
import { runAgentStream } from "@app/agent-runner.js";
import { executeSlashCommand } from "@app/command-executor.js";
import { resolveSlashCommand } from "@app/slash-command.js";
import { augmentPromptWithFiles } from "@lib/prompt-augmentation.js";
import { validateApiKey } from "@lib/api-key-format.js";
import {
  pruneImages,
  buildHumanMessageWithImages,
  type ImageRef,
} from "@lib/image-paste.js";
import type { AppState } from "@types";
import { defaultSystemPrompt, planSystemPrompt } from "@agent/index.js";

type PastedTextRef = {
  id: number;
  content: string;
  numLines: number;
};

const MAX_PILL_PREVIEW_LINES = 10;

function formatImageRef(id: number): string {
  return `[Image #${id}]`;
}

function formatPastedTextRef(id: number, numLines: number): string {
  if (numLines === 0) return `[Pasted text #${id}]`;
  return `[Pasted text #${id} +${numLines} lines]`;
}

function expandPastedTextRefs(
  input: string,
  pastedTexts: Map<number, PastedTextRef>,
): string {
  const refPattern = /\[Pasted text #(\d+)(?: \+\d+ lines)?\]/g;
  return input.replace(refPattern, (match, idStr) => {
    const id = parseInt(idStr, 10);
    const ref = pastedTexts.get(id);
    return ref ? ref.content : match;
  });
}

function prunePastedTexts(
  value: string,
  pastedTexts: Map<number, PastedTextRef>,
): void {
  for (const id of [...pastedTexts.keys()]) {
    if (
      !new RegExp(`\\[Pasted text #${id}(?: \\+\\d+ lines)?\\]`).test(value)
    ) {
      pastedTexts.delete(id);
    }
  }
}

export function useAppState(): AppState {
  const { exit } = useApp();
  const cols = useStore((store) => store.terminalCols);
  const apiKeys = useStore((store) => store.apiKeys);
  const setApiKey = useStore((store) => store.setApiKey);
  const clearApiKey = useStore((store) => store.clearApiKey);
  const currentModel = useStore((store) => store.modelConfig);
  const messages = useStore((store) => store.messages);
  const busy = useStore((store) => store.busy);
  const setBusy = useStore((store) => store.setBusy);
  const addMessage = useStore((store) => store.addMessage);
  const updateTokenUsage = useStore((store) => store.updateTokenUsage);
  const updateToolExecution = useStore((store) => store.updateToolExecution);
  const resetMessages = useStore((store) => store.resetMessages);
  const setModelConfig = useStore((store) => store.setModelConfig);
  const clearApiKeys = useStore((store) => store.clearApiKeys);

  const [mode, setMode] = useState<Mode>("agent");
  const [query, setQuery] = useState("");
  const [cursorOffset, setCursorOffsetState] = useState(0);
  const [sessionId] = useState(() => randomUUID());
  const conversationHistory = useRef<BaseMessage[]>([]);
  const [pendingApiKeyProvider, setPendingApiKeyProvider] =
    useState<Provider | null>(null);
  // Mirror of cursorOffset that updates synchronously. setState is async, so a
  // burst of onImagePaste calls (multi-image drag) would otherwise see a stale
  // offset on every call after the first and stack [Image #1][Image #2]…
  // back at the original cursor position instead of advancing through the
  // inserted text.
  const cursorOffsetRef = useRef(0);
  const setCursorOffset = useCallback((next: number) => {
    cursorOffsetRef.current = next;
    setCursorOffsetState(next);
  }, []);
  // Image attachments for the *current* prompt. Cleared on submit/reset.
  const pendingImages = useRef<Map<number, ImageRef>>(new Map());
  const pendingPastedTexts = useRef<Map<number, PastedTextRef>>(new Map());
  const nextRefId = useRef(1);
  // After we drop a pill ([Image #N], [Pasted text #N]) at the cursor, arm
  // this so the next printable character is auto-prefixed with a space — so
  // typing "look" after an image becomes "[Image #1] look" not "[Image #1]look".
  const pendingSpaceAfterPillRef = useRef(false);

  const providerNames: Record<Provider, string> = {
    openai: "OpenAI",
    anthropic: "Anthropic",
    google: "Google",
  };

  const hasApiKeyForProvider = (provider: Provider) => !!apiKeys[provider];

  // Menus
  const {
    showCommandMenu,
    filteredCommands,
    commandSelectionIndex,
    setCommandSelectionIndex,
    filterFromQuery: filterCommandsFromQuery,
    reset: resetCommandMenu,
  } = useCommandMenu();

  const {
    showModelMenu,
    filteredModels,
    modelSelectionIndex,
    setModelSelectionIndex,
    open: openModelMenu,
    close: closeModelMenu,
    filterFromQuery: filterModelsFromQuery,
    reset: resetModelMenu,
  } = useModelMenu();

  const {
    showApiKeysMenu,
    apiKeyItems,
    apiKeysSelectionIndex,
    setApiKeysSelectionIndex,
    open: openApiKeysMenu,
    close: closeApiKeysMenu,
  } = useApiKeysMenu(apiKeys);

  const {
    showFileSearchMenu,
    fileSearchMatches,
    fileSearchSelectionIndex,
    setFileSearchSelectionIndex,
    resetFileSearchMenu,
    handleAtReference,
    applyTabCompletion,
    applySubmitSelection,
  } = useFileSearchMenu();

  const currentOption = modelOptions.find(
    (option) =>
      option.name === currentModel.name &&
      option.effort === currentModel.effort,
  );
  const currentModelId = currentOption ? currentOption.id : 1;

  const busyText = useBusyText();

  // Splice text at the current cursor position. Used by paste handlers when
  // a placeholder ([Image #N], [Pasted text #N]) needs to be injected without
  // going through the normal keystroke path. Uses cursorOffsetRef so that a
  // burst of inserts in the same tick advances correctly.
  const insertTextAtCursor = useCallback(
    (text: string) => {
      const at = cursorOffsetRef.current;
      setQuery((prev) => prev.slice(0, at) + text + prev.slice(at));
      setCursorOffset(at + text.length);
    },
    [setCursorOffset],
  );

  const onChange = useCallback(
    (value: string) => {
      setQuery(value);
      // Drop image / paste refs whose placeholder was deleted by the user.
      pruneImages(value, pendingImages.current);
      prunePastedTexts(value, pendingPastedTexts.current);
      resetFileSearchMenu();
      resetCommandMenu();

      if (showModelMenu) {
        filterModelsFromQuery(value);
        return;
      }
      if (/@(\S*)$/.test(value)) {
        handleAtReference(value);
      } else if (value.startsWith("/")) {
        filterCommandsFromQuery(value);
      }
    },
    [
      showModelMenu,
      filterModelsFromQuery,
      handleAtReference,
      filterCommandsFromQuery,
      resetFileSearchMenu,
      resetCommandMenu,
    ],
  );

  const onChangeCursorOffset = useCallback(
    (offset: number) => {
      setCursorOffset(offset);
    },
    [setCursorOffset],
  );

  const onPaste = useCallback(
    (rawText: string) => {
      pendingSpaceAfterPillRef.current = false;
      const text = rawText.replace(/\r/g, "\n").replaceAll("\t", "    ");
      // For long pastes (>10 lines), tuck the content behind a [Pasted text #N]
      // placeholder so the input area stays readable. Short pastes go inline.
      const numLines = text.split("\n").length;
      if (numLines > MAX_PILL_PREVIEW_LINES) {
        const id = nextRefId.current++;
        pendingPastedTexts.current.set(id, { id, content: text, numLines });
        const prefix = needsSpaceBeforePill();
        insertTextAtCursor(prefix + formatPastedTextRef(id, numLines));
        pendingSpaceAfterPillRef.current = true;
      } else {
        insertTextAtCursor(text);
      }
    },
    [insertTextAtCursor],
  );

  const onImagePaste = useCallback(
    (
      base64: string,
      mediaType?: string,
      filename?: string,
      sourcePath?: string,
    ) => {
      const id = nextRefId.current++;
      pendingImages.current.set(id, {
        index: id,
        base64,
        mediaType: mediaType ?? "image/png",
        filename,
        sourcePath,
      });
      const prefix = needsSpaceBeforePill();
      insertTextAtCursor(prefix + formatImageRef(id));
      pendingSpaceAfterPillRef.current = true;
    },
    [insertTextAtCursor],
  );

  // Returns ' ' if the cursor is touching a non-space character on the left,
  // so a freshly-inserted pill never abuts an existing word.
  function needsSpaceBeforePill(): string {
    const at = cursorOffsetRef.current;
    if (at === 0) return "";
    const prev = query[at - 1];
    if (prev === undefined || /\s/.test(prev)) return "";
    return " ";
  }

  const onExit = useCallback(() => {
    exit();
  }, [exit]);

  // Lazy space after pill: the first printable character typed after we insert
  // a pill gets a leading space, so "look" after dropping an image yields
  // "[Image #1] look" instead of "[Image #1]look". Backspace, arrow keys, and
  // other navigation keys do not consume the lazy space.
  const inputFilter = useCallback((input: string, key: Key): string => {
    if (!pendingSpaceAfterPillRef.current) return input;
    // Only printable characters disarm the lazy-space; control keys (arrows,
    // delete, etc.) leave it pending.
    const isPrintable =
      input.length > 0 &&
      !key.return &&
      !key.escape &&
      !key.backspace &&
      !key.delete &&
      !key.tab &&
      !key.upArrow &&
      !key.downArrow &&
      !key.leftArrow &&
      !key.rightArrow &&
      !key.ctrl &&
      !key.meta &&
      input !== " ";
    if (!isPrintable) return input;
    pendingSpaceAfterPillRef.current = false;
    return " " + input;
  }, []);

  // Keybindings: only menu nav + tab handling. Escape, Ctrl+C, Ctrl+D, etc. are
  // owned by `useTextInput` (with double-press exit semantics) — we no longer
  // map a single Esc to "exit the app".
  useInput((input, key) => {
    if (key.escape && busy) {
      // Cancelling a busy stream still lives at the App level: TextInput will
      // run its escape handler too (clearing the draft), but during busy we
      // also stop the agent stream.
      setBusy(false);
      return;
    }

    if (
      showCommandMenu &&
      query === "/" &&
      (key.backspace || input.includes("\x7f"))
    ) {
      resetCommandMenu();
      setQuery("");
      setCursorOffset(0);
      return;
    }

    // Menu navigation (Up/Down). TextInput is told to skip up/down via
    // `disableCursorMovementForUpDownKeys` while a menu is open.
    if (showApiKeysMenu) {
      if (key.upArrow) {
        setApiKeysSelectionIndex(
          apiKeysSelectionIndex > 0 ? apiKeysSelectionIndex - 1 : apiKeysSelectionIndex,
        );
        return;
      }
      if (key.downArrow) {
        setApiKeysSelectionIndex(
          apiKeysSelectionIndex < apiKeyItems.length - 1
            ? apiKeysSelectionIndex + 1
            : apiKeysSelectionIndex,
        );
        return;
      }
    }

    if (showModelMenu) {
      if (key.upArrow) {
        setModelSelectionIndex((prev) => (prev > 0 ? prev - 1 : prev));
        return;
      }
      if (key.downArrow) {
        setModelSelectionIndex((prev) =>
          prev < filteredModels.length - 1 ? prev + 1 : prev,
        );
        return;
      }
    }

    if (showFileSearchMenu) {
      if (key.upArrow) {
        setFileSearchSelectionIndex((prev) => (prev > 0 ? prev - 1 : prev));
        return;
      }
      if (key.downArrow) {
        setFileSearchSelectionIndex((prev) =>
          prev < fileSearchMatches.length - 1 ? prev + 1 : prev,
        );
        return;
      }
    }

    if (showCommandMenu) {
      if (key.upArrow) {
        setCommandSelectionIndex((prev) => (prev > 0 ? prev - 1 : prev));
        return;
      }
      if (key.downArrow) {
        setCommandSelectionIndex((prev) => {
          if (filteredCommands.length === 0) return prev;
          return prev < filteredCommands.length - 1 ? prev + 1 : prev;
        });
        return;
      }
    }

    // Escape with menu open closes the menu.
    if (key.escape) {
      // First priority: cancel an in-progress API-key prompt. Without this
      // the user has no way out of onboarding once it starts — Enter on an
      // empty buffer just re-issues "API key cannot be empty.".
      if (pendingApiKeyProvider) {
        addMessage({
          author: "system",
          chunks: [{ kind: "text", text: "API key entry cancelled." }],
        });
        setPendingApiKeyProvider(null);
        pendingPastedTexts.current.clear();
        setQuery("");
        setCursorOffset(0);
        return;
      }
      if (showApiKeysMenu) {
        closeApiKeysMenu();
        setQuery("");
        setCursorOffset(0);
        return;
      }
      if (showModelMenu) {
        closeModelMenu();
        setQuery("");
        setCursorOffset(0);
        resetModelMenu();
        resetCommandMenu();
        return;
      }
      if (showCommandMenu) {
        resetCommandMenu();
        return;
      }
      if (showFileSearchMenu) {
        resetFileSearchMenu();
        return;
      }
      return;
    }

    if (key.tab) {
      if (showFileSearchMenu) {
        const next = applyTabCompletion(query);
        setQuery(next);
        setCursorOffset(next.length);
        return;
      }
      if (showCommandMenu) {
        const selected = filteredCommands[commandSelectionIndex];
        if (selected) {
          const next = `/${selected.name} `;
          setQuery(next);
          setCursorOffset(next.length);
        }
        resetCommandMenu();
        return;
      }
      if (!showModelMenu) {
        setMode((prev) => {
          const newMode: Mode = prev === "agent" ? "plan" : "agent";
          if (newMode === "plan") {
            conversationHistory.current[0] = new HumanMessage(planSystemPrompt);
          } else {
            conversationHistory.current[0] = new HumanMessage(
              defaultSystemPrompt,
            );
          }
          return newMode;
        });
      }
    }
  });

  const onSubmit = useCallback(
    async (value: string) => {
      // API key onboarding takes precedence — the user typed an API key, not a prompt.
      if (pendingApiKeyProvider) {
        // Expand a [Pasted text #N] placeholder before validating so the user
        // gets the same format-check whether their key was inserted inline or
        // tucked behind a placeholder by the long-paste handler.
        const expanded = expandPastedTextRefs(value, pendingPastedTexts.current);
        const key = expanded.trim();
        const reason = validateApiKey(pendingApiKeyProvider, key);
        if (reason) {
          addMessage({
            author: "system",
            chunks: [{ kind: "error", text: reason }],
          });
          setQuery("");
          setCursorOffset(0);
          return;
        }
        setApiKey(pendingApiKeyProvider, key);
        await storeApiKey(pendingApiKeyProvider, key);
        addMessage({
          author: "system",
          chunks: [
            {
              kind: "text",
              text: `${providerNames[pendingApiKeyProvider]} API key stored successfully.`,
            },
          ],
        });
        // Clear any pasted-text refs that were attached to this onboarding
        // submission — they're consumed and shouldn't leak into the next
        // prompt.
        pendingPastedTexts.current.clear();
        setPendingApiKeyProvider(null);
        setQuery("");
        setCursorOffset(0);
        return;
      }

      if (showApiKeysMenu) {
        const selectedItem = apiKeyItems[apiKeysSelectionIndex];
        if (!selectedItem) return;
        if (selectedItem.action === "delete") {
          await deleteStoredApiKey(selectedItem.provider);
          clearApiKey(selectedItem.provider);
          addMessage({
            author: "system",
            chunks: [
              {
                kind: "text",
                text: `${providerNames[selectedItem.provider]} API key removed.`,
              },
            ],
          });
          // Keep menu open; selection index will be re-clamped via apiKeyItems.
          setApiKeysSelectionIndex(0);
          setQuery("");
          setCursorOffset(0);
          return;
        }
        // 'set' action — prompt for the key inline.
        addMessage({
          author: "system",
          chunks: [
            {
              kind: "text",
              text: `Please enter your ${providerNames[selectedItem.provider]} API key:`,
            },
          ],
        });
        setPendingApiKeyProvider(selectedItem.provider);
        closeApiKeysMenu();
        setQuery("");
        setCursorOffset(0);
        return;
      }

      if (showModelMenu) {
        const selectedModel = filteredModels[modelSelectionIndex];
        if (!selectedModel) return;
        const newConfig: ModelConfig = {
          name: selectedModel.name,
          provider: selectedModel.provider,
          effort: selectedModel.effort,
        };

        if (!hasApiKeyForProvider(selectedModel.provider)) {
          addMessage({
            author: "system",
            chunks: [
              {
                kind: "text",
                text: `Please enter your ${providerNames[selectedModel.provider]} API key:`,
              },
            ],
          });
          setModelConfig(newConfig);
          await storeModelConfig(newConfig);
          setPendingApiKeyProvider(selectedModel.provider);
          closeModelMenu();
          resetModelMenu();
          setQuery("");
          setCursorOffset(0);
          resetCommandMenu();
          return;
        }

        setModelConfig(newConfig);
        await storeModelConfig(newConfig);
        addMessage({
          author: "system",
          chunks: [
            { kind: "text", text: `Model switched to ${selectedModel.label}` },
          ],
        });
        closeModelMenu();
        resetModelMenu();
        setQuery("");
        setCursorOffset(0);
        resetCommandMenu();
        return;
      }

      if (showFileSearchMenu) {
        const next = applySubmitSelection(value);
        setQuery(next);
        setCursorOffset(next.length);
        return;
      }

      const selected = showCommandMenu
        ? filteredCommands[commandSelectionIndex]
        : undefined;
      const effectiveValue = selected ? `/${selected.name}` : value;
      const trimmedValue = effectiveValue.trim();

      if (!trimmedValue || busy) {
        if (!trimmedValue) {
          setQuery("");
          setCursorOffset(0);
          resetCommandMenu();
        }
        return;
      }

      const currentProvider = currentModel.provider;
      if (!hasApiKeyForProvider(currentProvider)) {
        addMessage({
          author: "system",
          chunks: [
            {
              kind: "text",
              text: `Please enter your ${providerNames[currentProvider]} API key:`,
            },
          ],
        });
        setPendingApiKeyProvider(currentProvider);
        setQuery("");
        setCursorOffset(0);
        resetCommandMenu();
        return;
      }

      const slashCommand = resolveSlashCommand(trimmedValue);
      if (slashCommand.kind === "command") {
        const handled = await executeSlashCommand(
          slashCommand.command.name,
          {
            apiKeys,
            modelConfig: currentModel,
            addMessage,
            updateToolExecution,
            updateTokenUsage,
            setBusy,
          },
          {
            addMessage,
            resetMessages: () => {
              resetMessages();
              conversationHistory.current = [];
            },
            clearApiKeys,
            setShowModelMenu: openModelMenu,
            setFilteredModels: () => {},
            setModelSelectionIndex,
            setQuery: (next: string) => {
              setQuery(next);
              setCursorOffset(next.length);
            },
            exit,
            requestUiClear: () => {
              useStore.setState({ clearRequested: true });
              exit();
            },
            openApiKeysMenu: () => {
              openApiKeysMenu();
            },
            apiKeys,
            currentModel,
            sessionId,
          },
        );
        resetCommandMenu();
        if (handled) {
          setQuery("");
          setCursorOffset(0);
          return;
        }
      }

      resetCommandMenu();

      // Snapshot images/pasted text attached to this prompt before clearing.
      const promptImages = new Map(pendingImages.current);
      const promptPastedTexts = new Map(pendingPastedTexts.current);

      // Expand [Pasted text #N] placeholders into their full text. Image refs
      // are kept as-is — they become content blocks in the multipart message.
      const expandedValue = expandPastedTextRefs(value, promptPastedTexts);
      const finalPromptText = await augmentPromptWithFiles(expandedValue);
      const userMessage = await buildHumanMessageWithImages(
        finalPromptText,
        promptImages,
      );

      const imageCount = promptImages.size;
      const userBubbleText =
        imageCount > 0
          ? `${value}\n(attached ${imageCount} image${imageCount === 1 ? "" : "s"})`
          : value;
      addMessage({
        author: "user",
        timestamp: nowTime(),
        chunks: [{ kind: "text", text: userBubbleText }],
      });
      setQuery("");
      setCursorOffset(0);
      pendingImages.current.clear();
      pendingPastedTexts.current.clear();
      nextRefId.current = 1;

      await saveSession("last_session", conversationHistory.current);
      setBusy(true);
      try {
        const systemPrompt =
          mode === "plan" ? planSystemPrompt : defaultSystemPrompt;
        await runAgentStream(
          {
            apiKeys,
            modelConfig: currentModel,
            addMessage,
            updateToolExecution,
            updateTokenUsage,
            setBusy,
          },
          conversationHistory,
          userMessage,
          systemPrompt,
        );
      } catch {
        // runAgentStream already reports errors via addMessage.
      } finally {
        setBusy(false);
      }
    },
    [
      apiKeys,
      busy,
      closeModelMenu,
      currentModel,
      filteredCommands,
      filteredModels,
      commandSelectionIndex,
      modelSelectionIndex,
      mode,
      openModelMenu,
      addMessage,
      resetCommandMenu,
      resetMessages,
      setBusy,
      setModelConfig,
      setModelSelectionIndex,
      showCommandMenu,
      showFileSearchMenu,
      showModelMenu,
      showApiKeysMenu,
      apiKeyItems,
      apiKeysSelectionIndex,
      closeApiKeysMenu,
      openApiKeysMenu,
      setApiKeysSelectionIndex,
      clearApiKey,
      updateTokenUsage,
      updateToolExecution,
      exit,
      applySubmitSelection,
      pendingApiKeyProvider,
      setApiKey,
      clearApiKeys,
      sessionId,
      resetModelMenu,
    ],
  );

  return {
    cols,
    messages,
    busy,
    mode,
    currentModel,
    currentModelId,
    busyText,
    query,
    onChange,
    onSubmit,
    setQuery,
    cursorOffset,
    onChangeCursorOffset,
    onPaste,
    onImagePaste,
    onExit,
    inputFilter,
    showCommandMenu,
    filteredCommands,
    commandSelectionIndex,
    setCommandSelectionIndex,
    showModelMenu,
    filteredModels,
    modelSelectionIndex,
    setModelSelectionIndex,
    showFileSearchMenu,
    fileSearchMatches,
    fileSearchSelectionIndex,
    setFileSearchSelectionIndex,
    showApiKeysMenu,
    apiKeyItems,
    apiKeysSelectionIndex,
    setApiKeysSelectionIndex,
  };
}
