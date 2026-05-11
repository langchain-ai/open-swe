import { useEffect, useState } from "react";
import { Box, Text, useApp } from "ink";
import { Spinner } from "./components/Spinner.js";
import { themeColor } from "./theme.js";
import { applyToLocal, validateBundle, type HandoffBundle } from "@lib/handoff";
import type { ApiClient } from "@lib/api-client";

type Props = {
  api: ApiClient;
  direction: "local" | "cloud";
  thread_id?: string;
  onAttach?: (tid: string) => void;
};

// Top-level `openswe handoff --to <dir>` entry point.
//
// --to local : requires --thread; calls the backend's handoff endpoint and
//              applies the bundle to the current working directory.
// --to cloud : not runnable from outside a session — there's no local
//              conversation to export. Tell the user to use the in-session
//              slash command instead.
export const HandoffScreen = ({ api, direction, thread_id, onAttach }: Props) => {
  const { exit } = useApp();
  const [status, setStatus] = useState<
    | { kind: "running"; text: string }
    | { kind: "ok"; text: string }
    | { kind: "error"; text: string }
  >({ kind: "running", text: "Starting handoff…" });

  const subtle = themeColor("subtle");
  const success = themeColor("success");
  const errColor = themeColor("error");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (direction === "cloud") {
        setStatus({
          kind: "error",
          text:
            "Run `/handoff cloud` from inside a local session — there is no " +
            "local conversation to export when invoked from outside.",
        });
        setTimeout(() => exit(), 50);
        return;
      }
      if (!thread_id) {
        setStatus({
          kind: "error",
          text: "Missing --thread <id>. Usage: openswe handoff --to local --thread <id>",
        });
        setTimeout(() => exit(), 50);
        return;
      }
      setStatus({ kind: "running", text: "Pausing cloud run and exporting state…" });
      let bundle: HandoffBundle;
      try {
        bundle = (await api.exportHandoff(thread_id)) as HandoffBundle;
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        setStatus({ kind: "error", text: `Handoff request failed: ${message}` });
        setTimeout(() => exit(), 50);
        return;
      }
      const v = validateBundle(bundle);
      if (!v.ok) {
        setStatus({ kind: "error", text: `Invalid bundle: ${v.error}` });
        setTimeout(() => exit(), 50);
        return;
      }
      try {
        await applyToLocal(bundle, process.cwd());
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        setStatus({ kind: "error", text: `Could not apply to local workdir: ${message}` });
        setTimeout(() => exit(), 50);
        return;
      }
      if (cancelled) return;
      setStatus({
        kind: "ok",
        text:
          "Cloud state applied to your working directory. " +
          "The cloud sandbox is paused (not destroyed). " +
          "Continue locally by running `openswe` in this directory.",
      });
      // Note: onAttach is intentionally unused here — DESIGN.md says the
      // user continues locally after handoff --to local; there's no Attach
      // screen to transition to.
      if (onAttach) {
        // referenced to satisfy lint if needed; intentional no-op
      }
      setTimeout(() => exit(), 50);
    })();
    return () => {
      cancelled = true;
    };
  }, [api, direction, thread_id, exit, onAttach]);

  if (status.kind === "running") {
    return (
      <Box flexDirection="row" paddingY={1}>
        <Spinner />
        <Text color={subtle}> {status.text}</Text>
      </Box>
    );
  }
  if (status.kind === "ok") {
    return (
      <Box paddingY={1}>
        <Text color={success}>{status.text}</Text>
      </Box>
    );
  }
  return (
    <Box paddingY={1}>
      <Text color={errColor}>{status.text}</Text>
    </Box>
  );
};
