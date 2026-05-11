import { useCallback, useEffect, useRef, useState } from "react";
import { Box, Static, Text, useApp, useInput } from "ink";
import { Message } from "./components/Message.js";
import { PromptInput } from "./components/PromptInput.js";
import { BusyLine } from "./components/BusyLine.js";
import { Spinner } from "./components/Spinner.js";
import { themeColor } from "./theme.js";
import { useStore } from "@app/store.js";
import { CloudRunner } from "@app/cloud-runner.js";
import type { ApiClient } from "@lib/api-client.js";
import type { DeploymentConfig } from "@lib/api-types.js";
import { nowTime } from "@lib/time.js";

type Props = {
  api: ApiClient;
  thread_id: string;
  deployment: DeploymentConfig;
  onDetach: () => void;
};

const HELP_LINES = [
  "/detach            — close stream, leave run running",
  "/interrupt         — interrupt current step",
  "/handoff cloud     — (not yet available)",
  "/handoff local     — (not yet available)",
  "/whoami            — show logged-in user",
  "/help              — show this help",
];

const truncateThreadId = (id: string): string => {
  if (id.length <= 12) return id;
  return `${id.slice(0, 8)}…${id.slice(-3)}`;
};

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
  const [topRepoBranch, setTopRepoBranch] = useState<string>("");
  const [topSource, setTopSource] = useState<string>("cli");
  const [streamStatus, setStreamStatus] = useState<
    "connecting" | "live" | "error" | "closed"
  >("connecting");
  const [streamError, setStreamError] = useState<string>("");

  const runnerRef = useRef<CloudRunner | null>(null);

  const subtle = themeColor("subtle");
  const success = themeColor("success");
  const warning = themeColor("warning");
  const errColor = themeColor("error");
  const inactive = themeColor("inactive");
  const brand = themeColor("brand");

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
          if (s.source) setTopSource(s.source);
          if (s.repo && s.branch) setTopRepoBranch(`${s.repo}:${s.branch}`);
          else if (s.repo) setTopRepoBranch(s.repo);
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
    void runner.attach();
    return () => {
      runner.detach();
      runnerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, thread_id]);

  const handleSlashCommand = useCallback(
    (raw: string): boolean => {
      const cmd = raw.trim();
      if (!cmd.startsWith("/")) return false;
      const [head, ...rest] = cmd.slice(1).split(/\s+/);
      const argsLine = rest.join(" ");
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
        case "handoff": {
          addMessage({
            author: "system",
            chunks: [
              {
                kind: "text",
                text: `Handoff${argsLine ? ` (${argsLine})` : ""} is not yet available in this version.`,
              },
            ],
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

  useInput((input, key) => {
    if (key.escape && busy) {
      void runnerRef.current?.interrupt();
    }
  });

  const staticMessages = messages.length > 1 ? messages.slice(0, -1) : [];
  const liveMessage =
    messages.length > 0 ? messages[messages.length - 1] : null;

  const streamIndicator = (() => {
    if (streamStatus === "connecting") return { label: "connecting", color: warning };
    if (streamStatus === "live") return { label: "live", color: success };
    if (streamStatus === "closed") return { label: "closed", color: inactive };
    return { label: "error", color: errColor };
  })();

  return (
    <Box flexDirection="column">
      <Box
        borderStyle="round"
        borderColor={brand}
        paddingX={1}
        marginBottom={1}
        flexDirection="row"
      >
        <Text color={brand} bold>
          attach
        </Text>
        <Text color={subtle}>  ·  </Text>
        <Text color={subtle}>thread </Text>
        <Text>{truncateThreadId(thread_id)}</Text>
        <Text color={subtle}>  ·  source </Text>
        <Text>{topSource}</Text>
        {topRepoBranch ? (
          <>
            <Text color={subtle}>  ·  </Text>
            <Text>{topRepoBranch}</Text>
          </>
        ) : null}
        <Text color={subtle}>  ·  </Text>
        <Text color={streamIndicator.color}>{streamIndicator.label}</Text>
        <Text color={subtle}>  ·  </Text>
        <Text color={subtle}>
          {deployment.github_login} @ {deployment.backend_url}
        </Text>
      </Box>

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
          onImagePaste={() => {
            /* not supported in cloud attach yet */
          }}
          onExit={exit}
          columns={cols}
          mode="agent"
          showCommandMenu={false}
          filteredCommands={[]}
          commandSelectionIndex={0}
          showFileSearchMenu={false}
          fileSearchMatches={[]}
          fileSearchSelectionIndex={0}
          showModelMenu={false}
          filteredModels={[]}
          modelSelectionIndex={0}
          currentModelId={1}
          showApiKeysMenu={false}
          apiKeyItems={[]}
          apiKeysSelectionIndex={0}
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
