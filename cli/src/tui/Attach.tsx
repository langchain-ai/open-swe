import { useCallback, useEffect, useRef, useState } from "react";
import { Box, Static, Text, useApp, useInput } from "ink";
import { Message } from "./components/Message.js";
import { PromptInput } from "./components/PromptInput.js";
import { BusyLine } from "./components/BusyLine.js";
import { Spinner } from "./components/Spinner.js";
import { themeColor } from "./theme.js";
import { useStore } from "@app/store.js";
import { CloudRunner } from "@app/cloud-runner.js";
import type { ApiClient } from "@lib/api-client";
import type { DeploymentConfig } from "@lib/api-types";
import { nowTime } from "@lib/time";
import { loadCursor, saveCursor } from "@lib/cursor-store";

type Props = {
  api: ApiClient;
  thread_id: string;
  deployment: DeploymentConfig;
  onDetach: () => void;
};

const HELP_LINES = [
  "/detach            — close stream, leave run running",
  "/interrupt         — interrupt current step (or press Esc while busy)",
  "/whoami            — show logged-in user",
  "/help              — show this help",
];

export const Attach = ({ api, thread_id, deployment, onDetach }: Props) => {
  const { exit } = useApp();
  const cols = useStore((s) => s.terminalCols);
  const messages = useStore((s) => s.messages);
  const busy = useStore((s) => s.busy);
  const setBusy = useStore((s) => s.setBusy);
  const addMessage = useStore((s) => s.addMessage);
  const updateToolExecution = useStore((s) => s.updateToolExecution);
  const updateTokenUsage = useStore((s) => s.updateTokenUsage);

  const [query, setQuery] = useState("");
  const [cursorOffset, setCursorOffset] = useState(0);
  const [queuedAt, setQueuedAt] = useState<number | null>(null);
  const [streamStatus, setStreamStatus] = useState<
    "connecting" | "live" | "error" | "closed"
  >("connecting");
  const [streamError, setStreamError] = useState<string>("");

  const runnerRef = useRef<CloudRunner | null>(null);

  const subtle = themeColor("subtle");
  const warning = themeColor("warning");
  const errColor = themeColor("error");

  useEffect(() => {
    const runner = new CloudRunner(api, thread_id, {
      addMessage,
      updateToolExecution,
      updateTokenUsage,
      setBusy,
      onStatus: (s) => {
        if (s.kind === "connecting") {
          setStreamStatus("connecting");
        } else if (s.kind === "connected") {
          setStreamStatus("live");
        } else if (s.kind === "event") {
          setQueuedAt((prev) => {
            if (prev === null) return prev;
            const ev = s.event_time;
            return ev !== undefined && ev >= prev ? null : prev;
          });
        } else if (s.kind === "closed") {
          setStreamStatus("closed");
        } else if (s.kind === "error") {
          setStreamStatus("error");
          setStreamError(s.message);
        }
      },
    });
    runnerRef.current = runner;
    void (async () => {
      const since = await loadCursor(thread_id);
      await runner.attach({ since });
    })();
    return () => {
      const last = runner.getLastEventIso();
      if (last) void saveCursor(thread_id, last);
      runner.detach();
      runnerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, thread_id]);

  const handleSlashCommand = useCallback(
    (raw: string): boolean => {
      const cmd = raw.trim();
      if (!cmd.startsWith("/")) return false;
      const [head] = cmd.slice(1).split(/\s+/);
      switch (head) {
        case "detach": {
          runnerRef.current?.detach();
          onDetach();
          return true;
        }
        case "interrupt": {
          void runnerRef.current?.interrupt();
          addMessage({
            author: "system",
            chunks: [{ kind: "text", text: "Interrupt requested." }],
          });
          return true;
        }
        case "whoami": {
          addMessage({
            author: "system",
            chunks: [
              {
                kind: "text",
                text: `Logged in as ${deployment.github_login} @ ${deployment.backend_url}`,
              },
            ],
          });
          return true;
        }
        case "help": {
          addMessage({
            author: "system",
            chunks: [
              { kind: "text", text: "Available commands:" },
              { kind: "list", lines: HELP_LINES },
            ],
          });
          return true;
        }
        default:
          return false;
      }
    },
    [addMessage, deployment.backend_url, deployment.github_login, onDetach],
  );

  const onSubmit = useCallback(
    async (value: string) => {
      const trimmed = value.trim();
      if (!trimmed) {
        setQuery("");
        setCursorOffset(0);
        return;
      }
      if (trimmed.startsWith("/")) {
        if (handleSlashCommand(trimmed)) {
          setQuery("");
          setCursorOffset(0);
          return;
        }
      }
      addMessage({
        author: "user",
        timestamp: nowTime(),
        chunks: [{ kind: "text", text: value }],
      });
      setQuery("");
      setCursorOffset(0);
      try {
        await runnerRef.current?.sendMessage(trimmed);
        setQueuedAt(Date.now());
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        addMessage({
          author: "system",
          chunks: [{ kind: "error", text: `Failed to send message: ${message}` }],
        });
      }
    },
    [addMessage, handleSlashCommand],
  );

  useInput((_input, key) => {
    if (key.escape && busy) {
      void runnerRef.current?.interrupt();
    }
  });

  const staticMessages = messages.length > 1 ? messages.slice(0, -1) : [];
  const liveMessage =
    messages.length > 0 ? messages[messages.length - 1] : null;

  return (
    <Box flexDirection="column">
      <Static
        items={staticMessages.map((message) => ({
          kind: "message" as const,
          key: message.id,
          message,
        }))}
      >
        {(item) => <Message key={item.key} message={item.message} />}
      </Static>

      {liveMessage ? <Message message={liveMessage} /> : null}

      {busy ? <BusyLine label="thinking" /> : null}

      {streamStatus === "connecting" ? (
        <Box marginTop={1}>
          <Spinner />
          <Text color={subtle}> Connecting to {deployment.backend_url}…</Text>
        </Box>
      ) : null}
      {streamStatus === "error" ? (
        <Box marginTop={1}>
          <Text color={errColor}>Stream error: {streamError}</Text>
        </Box>
      ) : null}

      <Box marginTop={1} flexDirection="column">
        <PromptInput
          query={query}
          onChange={setQuery}
          onSubmit={onSubmit}
          cursorOffset={cursorOffset}
          onChangeCursorOffset={setCursorOffset}
          onPaste={(text) => {
            setQuery((q) => q + text);
            setCursorOffset((c) => c + text.length);
          }}
          onExit={exit}
          columns={cols}
          placeholder="Send a follow-up message…"
        />
        <Box paddingX={1}>
          {queuedAt !== null ? (
            <Text color={warning}>
              queued — will be delivered before next step
            </Text>
          ) : (
            <Text color={subtle}>
              {deployment.github_login} @ {deployment.backend_url}  ·  /detach to leave  ·  /help for commands
            </Text>
          )}
        </Box>
      </Box>
    </Box>
  );
};
