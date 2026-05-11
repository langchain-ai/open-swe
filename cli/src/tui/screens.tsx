import React, { useEffect, useState } from "react";
import { Box, Text, useApp, useInput } from "ink";
import { App } from "./App.js";
import { Login } from "./Login.js";
import { RunsList } from "./RunsList.js";
import { Attach } from "./Attach.js";
import { NewCloud } from "./NewCloud.js";
import { HandoffScreen } from "./HandoffScreen.js";
import { Spinner } from "./components/Spinner.js";
import { themeColor } from "./theme.js";
import { ApiClient } from "@lib/api-client";
import { getActiveDeployment } from "@lib/config";
import type { DeploymentConfig } from "@lib/api-types";
import type { ParsedArgs } from "@lib/cli-args";
import { ExpiredAuthContext, wrapApi } from "./ExpiredAuthContext.js";

export type Screen =
  | "local"
  | "login"
  | "runs"
  | "attach"
  | "new-cloud"
  | "handoff";

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
    case "handoff":
      return "handoff";
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
  const [expiredDeployment, setExpiredDeployment] =
    useState<DeploymentConfig | null>(null);
  const [prevState, setPrevState] = useState<ResolvedState | null>(null);
  const [reloginActive, setReloginActive] = useState(false);

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

  const markExpired = (d: DeploymentConfig) => {
    setExpiredDeployment((prev) => prev ?? d);
    if (state.kind === "screen" && !prevState) {
      setPrevState(state);
    }
  };

  useInput(
    (input, key) => {
      if (!expiredDeployment || reloginActive) return;
      if (input === "r" || input === "R" || key.return) {
        setReloginActive(true);
      } else if (input === "q" || input === "Q" || key.escape) {
        exit();
      }
    },
    { isActive: expiredDeployment !== null && !reloginActive },
  );

  if (expiredDeployment && reloginActive) {
    return (
      <ExpiredAuthContext.Provider value={{ markExpired }}>
        <Login
          initialBackendUrl={expiredDeployment.backend_url}
          onComplete={(d) => {
            setExpiredDeployment(null);
            setReloginActive(false);
            const restore = prevState;
            setPrevState(null);
            if (restore && restore.kind === "screen") {
              setState({ ...restore, deployment: d });
            } else {
              setState({ kind: "screen", screen: "runs", deployment: d });
            }
          }}
        />
      </ExpiredAuthContext.Provider>
    );
  }

  if (expiredDeployment) {
    const brand = themeColor("brand");
    const subtle = themeColor("subtle");
    return (
      <Box flexDirection="column" paddingY={1}>
        <Box
          borderStyle="round"
          borderColor={brand}
          paddingX={2}
          flexDirection="column"
        >
          <Text bold color={brand}>
            Session expired
          </Text>
          <Text color={subtle}>
            Your session for {expiredDeployment.backend_url} has expired.
          </Text>
          <Box marginTop={1}>
            <Text>Re-login to </Text>
            <Text bold>{expiredDeployment.backend_url}</Text>
            <Text>?</Text>
          </Box>
          <Box marginTop={1}>
            <Text color={subtle}>Press </Text>
            <Text bold>r</Text>
            <Text color={subtle}> or </Text>
            <Text bold>Enter</Text>
            <Text color={subtle}> to re-login, </Text>
            <Text bold>q</Text>
            <Text color={subtle}> to quit.</Text>
          </Box>
        </Box>
      </Box>
    );
  }

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
    return (
      <App
        onHandoffToCloud={(tid) => {
          // After a successful /handoff cloud the local agent is done; jump
          // to attach on the new cloud thread. RootScreen does not preload
          // the deployment in local mode, so fetch it now — useAppState
          // already verified one is configured before calling us.
          void (async () => {
            const d = deployment ?? (await getActiveDeployment());
            if (!d) return;
            setPendingThreadId(tid);
            setState({
              kind: "screen",
              screen: "attach",
              deployment: d,
              thread_id: tid,
            });
          })();
        }}
      />
    );
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

  const rawApi = new ApiClient(deployment.backend_url, deployment.session_token);
  const api = wrapApi(rawApi, deployment, markExpired);

  const wrapped = (children: React.ReactNode) => (
    <ExpiredAuthContext.Provider value={{ markExpired }}>
      {children}
    </ExpiredAuthContext.Provider>
  );

  if (screen === "runs") {
    return wrapped(
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
      />,
    );
  }

  if (screen === "new-cloud") {
    // Fast-path: if all three args were provided on the command line, kick
    // off creation immediately without showing the form.
    const haveAllArgs = !!(args.repo && args.branch && args.prompt);
    if (haveAllArgs && !creating && !createError) {
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
      return wrapped(
        <Box flexDirection="column" paddingY={1}>
          <Box>
            <Spinner />
            <Text color={themeColor("subtle")}> Creating cloud run…</Text>
          </Box>
        </Box>,
      );
    }
    return wrapped(
      <NewCloud
        api={api}
        initialRepo={args.repo}
        initialBranch={args.branch ?? "main"}
        initialPrompt={args.prompt}
        model={args.model}
        agent={args.agent}
        onCreated={(tid) => {
          setState({
            kind: "screen",
            screen: "attach",
            deployment,
            thread_id: tid,
          });
        }}
        onCancel={() => {
          setState({ kind: "screen", screen: "runs", deployment });
        }}
      />,
    );
  }

  if (screen === "handoff") {
    return wrapped(
      <HandoffScreen
        api={api}
        direction={args.handoff_to ?? "local"}
        thread_id={args.thread_id}
      />,
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
    return wrapped(
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
      />,
    );
  }

  return null;
};
