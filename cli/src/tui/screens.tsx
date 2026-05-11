import { useEffect, useState } from "react";
import { Box, Text, useApp } from "ink";
import { App } from "./App.js";
import { Login } from "./Login.js";
import { RunsList } from "./RunsList.js";
import { Attach } from "./Attach.js";
import { Spinner } from "./components/Spinner.js";
import { themeColor } from "./theme.js";
import { ApiClient } from "@lib/api-client";
import { getActiveDeployment } from "@lib/config";
import type { DeploymentConfig } from "@lib/api-types";
import type { ParsedArgs } from "@lib/cli-args";

export type Screen = "local" | "login" | "runs" | "attach" | "new-cloud";

type Props = {
  args: ParsedArgs;
};

type ResolvedState =
  | { kind: "loading" }
  | { kind: "screen"; screen: Screen; deployment: DeploymentConfig | null; thread_id?: string };

const screenFromArgs = (args: ParsedArgs): Screen => {
  switch (args.command) {
    case "login":
      return "login";
    case "runs":
      return "runs";
    case "attach":
      return "attach";
    case "new-cloud":
      return "new-cloud";
    case "local":
    default:
      return "local";
  }
};

export const RootScreen = ({ args }: Props) => {
  const { exit } = useApp();
  const [state, setState] = useState<ResolvedState>({ kind: "loading" });
  const [pendingThreadId, setPendingThreadId] = useState<string | undefined>(
    args.thread_id,
  );
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const requested = screenFromArgs(args);
      // Local mode needs no deployment.
      if (requested === "local") {
        if (!cancelled) {
          setState({ kind: "screen", screen: "local", deployment: null });
        }
        return;
      }
      const deployment = await getActiveDeployment(args.backend_url);
      if (cancelled) return;
      if (requested === "login") {
        setState({
          kind: "screen",
          screen: "login",
          deployment: deployment ?? null,
        });
        return;
      }
      if (!deployment) {
        // No deployment for a cloud action: force login first.
        setState({ kind: "screen", screen: "login", deployment: null });
        return;
      }
      setState({
        kind: "screen",
        screen: requested,
        deployment,
        thread_id: args.thread_id,
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [args]);

  if (state.kind === "loading") {
    return (
      <Box>
        <Spinner />
        <Text color={themeColor("subtle")}> Loading…</Text>
      </Box>
    );
  }

  const { screen, deployment } = state;

  if (screen === "local") {
    return <App />;
  }

  if (screen === "login") {
    return (
      <Login
        initialBackendUrl={args.backend_url ?? deployment?.backend_url}
        onComplete={(d) => {
          // After login, advance to the originally-requested cloud screen
          // (or runs if just `login`).
          const next = screenFromArgs(args);
          if (next === "login") {
            setState({ kind: "screen", screen: "runs", deployment: d });
          } else if (next === "local") {
            setState({ kind: "screen", screen: "local", deployment: d });
          } else {
            setState({
              kind: "screen",
              screen: next,
              deployment: d,
              thread_id: args.thread_id,
            });
          }
        }}
      />
    );
  }

  if (!deployment) {
    return (
      <Box>
        <Text color={themeColor("error")}>No deployment configured.</Text>
      </Box>
    );
  }

  const api = new ApiClient(deployment.backend_url, deployment.session_token);

  if (screen === "runs") {
    return (
      <RunsList
        api={api}
        onAttach={(tid) => {
          setPendingThreadId(tid);
          setState({
            kind: "screen",
            screen: "attach",
            deployment,
            thread_id: tid,
          });
        }}
        onNew={() => {
          setState({
            kind: "screen",
            screen: "new-cloud",
            deployment,
          });
        }}
        onQuit={() => exit()}
      />
    );
  }

  if (screen === "new-cloud") {
    // If the user provided enough args, kick off creation; otherwise show
    // a small notice (a richer form is out of scope for this PR).
    if (!creating && args.repo && args.branch && args.prompt) {
      setCreating(true);
      void (async () => {
        try {
          const res = await api.createRun({
            repo: args.repo!,
            branch: args.branch!,
            prompt: args.prompt!,
            model: args.model,
            agent: args.agent,
          });
          setState({
            kind: "screen",
            screen: "attach",
            deployment,
            thread_id: res.thread_id,
          });
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          setCreateError(message);
        } finally {
          setCreating(false);
        }
      })();
    }
    return (
      <Box flexDirection="column" paddingY={1}>
        {creating ? (
          <Box>
            <Spinner />
            <Text color={themeColor("subtle")}> Creating cloud run…</Text>
          </Box>
        ) : null}
        {createError ? (
          <Text color={themeColor("error")}>
            Failed to create run: {createError}
          </Text>
        ) : null}
        {!creating && !args.repo ? (
          <Text color={themeColor("subtle")}>
            Usage: openswe new --cloud --repo owner/repo --branch foo &quot;prompt&quot;
          </Text>
        ) : null}
      </Box>
    );
  }

  if (screen === "attach") {
    const tid = state.thread_id ?? pendingThreadId;
    if (!tid) {
      return (
        <Box>
          <Text color={themeColor("error")}>
            Missing thread_id. Pass one as `openswe attach &lt;thread_id&gt;`.
          </Text>
        </Box>
      );
    }
    return (
      <Attach
        api={api}
        thread_id={tid}
        deployment={deployment}
        onDetach={() => {
          setState({
            kind: "screen",
            screen: "runs",
            deployment,
          });
        }}
      />
    );
  }

  return null;
};
