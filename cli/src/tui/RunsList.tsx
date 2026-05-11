import { useEffect, useState, useCallback } from "react";
import { Box, Text, useInput } from "ink";
import { Spinner } from "./components/Spinner.js";
import { themeColor } from "./theme.js";
import { ARROW_RIGHT_THIN } from "./figures.js";
import type { ApiClient } from "@lib/api-client.js";
import type { RunSource, RunStatus, RunSummary } from "@lib/api-types.js";

type Props = {
  api: ApiClient;
  onAttach: (thread_id: string) => void;
  onNew: () => void;
  onQuit: () => void;
};

const sourceIcon = (source: RunSource): string => {
  switch (source) {
    case "github":
      return "GH";
    case "slack":
      return "SL";
    case "linear":
      return "LN";
    case "cli":
      return "CLI";
  }
};

const statusColor = (status: RunStatus): keyof typeof STATUS_COLOR_KEYS => {
  return status;
};

const STATUS_COLOR_KEYS = {
  running: "warning",
  idle: "suggestion",
  completed: "success",
  error: "error",
} as const;

const relativeTime = (iso: string): string => {
  const d = new Date(iso).getTime();
  if (Number.isNaN(d)) return "";
  const diffSec = Math.max(0, Math.round((Date.now() - d) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  const min = Math.round(diffSec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  return `${day}d ago`;
};

const REFRESH_INTERVAL_MS = 10_000;

export const RunsList = ({ api, onAttach, onNew, onQuit }: Props) => {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [error, setError] = useState<string>("");
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [loading, setLoading] = useState<boolean>(true);

  const brand = themeColor("brand");
  const subtle = themeColor("subtle");
  const inactive = themeColor("inactive");
  const errColor = themeColor("error");
  const selectionBg = themeColor("selectionBg");

  const refresh = useCallback(async () => {
    try {
      const next = await api.listRuns();
      setRuns(next);
      setError("");
      setSelectedIdx((idx) => Math.min(idx, Math.max(0, next.length - 1)));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => {
      void refresh();
    }, REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  useInput((input, key) => {
    if (loading || !runs) {
      if (input === "q" || input === "Q") onQuit();
      return;
    }
    if (key.upArrow) {
      setSelectedIdx((i) => (i > 0 ? i - 1 : i));
      return;
    }
    if (key.downArrow) {
      setSelectedIdx((i) => (runs.length > 0 ? Math.min(i + 1, runs.length - 1) : 0));
      return;
    }
    if (key.return) {
      const sel = runs[selectedIdx];
      if (sel) onAttach(sel.thread_id);
      return;
    }
    if (input === "r" || input === "R") {
      setLoading(true);
      void refresh();
      return;
    }
    if (input === "n" || input === "N") {
      onNew();
      return;
    }
    if (input === "q" || input === "Q") {
      onQuit();
      return;
    }
  });

  return (
    <Box flexDirection="column" paddingY={1}>
      <Box
        borderStyle="round"
        borderColor={brand}
        paddingX={2}
        flexDirection="column"
        marginBottom={1}
      >
        <Box>
          <Text color={brand}>{ARROW_RIGHT_THIN}_ </Text>
          <Text bold>Active Runs</Text>
        </Box>
        <Text color={subtle}>
          {`↑/↓ navigate  ·  Enter attach  ·  n new  ·  r refresh  ·  q quit`}
        </Text>
      </Box>

      {loading ? (
        <Box>
          <Spinner />
          <Text color={subtle}> Loading runs…</Text>
        </Box>
      ) : null}

      {!loading && error ? (
        <Box flexDirection="column">
          <Text color={errColor}>Failed to load runs: {error}</Text>
          <Text color={subtle}>Press r to retry.</Text>
        </Box>
      ) : null}

      {!loading && !error && runs && runs.length === 0 ? (
        <Text color={subtle}>No active runs. Press &apos;n&apos; to start one.</Text>
      ) : null}

      {!loading && !error && runs && runs.length > 0 ? (
        <Box flexDirection="column">
          {runs.map((run, idx) => {
            const isSelected = idx === selectedIdx;
            const statusKey = statusColor(run.status);
            const statColor = themeColor(STATUS_COLOR_KEYS[statusKey]);
            const repoBranch =
              run.repo && run.branch
                ? `${run.repo}:${run.branch}`
                : run.repo ?? "";
            return (
              <Box key={run.thread_id} backgroundColor={isSelected ? selectionBg : undefined}>
                <Text color={isSelected ? brand : inactive}>
                  {isSelected ? "› " : "  "}
                </Text>
                <Text color={inactive}>[{sourceIcon(run.source)}] </Text>
                <Text>{run.title} </Text>
                {repoBranch ? <Text color={subtle}>{repoBranch}  </Text> : null}
                <Text color={statColor}>{run.status}</Text>
                <Text color={subtle}>  {relativeTime(run.last_event_at)}</Text>
              </Box>
            );
          })}
        </Box>
      ) : null}
    </Box>
  );
};
