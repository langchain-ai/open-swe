import { useEffect, useState, useCallback } from "react";
import { Box, Text, useApp, useInput } from "ink";
import { TextInput } from "./components/TextInput/index.js";
import { Spinner } from "./components/Spinner.js";
import { themeColor } from "./theme.js";
import { ARROW_RIGHT_THIN, CHECK, CROSS } from "./figures.js";
import { setDeployment } from "@lib/config.js";
import { login as runLogin } from "@lib/auth-flow.js";
import type { DeploymentConfig } from "@lib/api-types.js";

type Phase = "input" | "running" | "error" | "done";

type Props = {
  initialBackendUrl?: string;
  onComplete: (d: DeploymentConfig) => void;
};

export const Login = ({ initialBackendUrl, onComplete }: Props) => {
  const { exit } = useApp();
  const [backendUrl, setBackendUrl] = useState<string>(initialBackendUrl ?? "");
  const [cursorOffset, setCursorOffset] = useState<number>(
    (initialBackendUrl ?? "").length,
  );
  const [phase, setPhase] = useState<Phase>(
    initialBackendUrl && initialBackendUrl.length > 0 ? "running" : "input",
  );
  const [status, setStatus] = useState<string>("");
  const [statusHistory, setStatusHistory] = useState<string[]>([]);
  const [error, setError] = useState<string>("");

  const brand = themeColor("brand");
  const subtle = themeColor("subtle");
  const success = themeColor("success");
  const errColor = themeColor("error");
  const suggestion = themeColor("suggestion");

  const startLogin = useCallback(
    async (url: string) => {
      const trimmed = url.trim().replace(/\/+$/, "");
      if (!trimmed) {
        setError("Backend URL cannot be empty.");
        setPhase("error");
        return;
      }
      setError("");
      setStatusHistory([]);
      setStatus(`Contacting ${trimmed}…`);
      setPhase("running");
      try {
        const deployment = await runLogin(trimmed, {
          onStatus: (msg: string) => {
            setStatusHistory((prev) => [...prev, msg]);
            setStatus(msg);
          },
        });
        setStatusHistory((prev) => [...prev, "Done"]);
        setStatus("Done");
        setPhase("done");
        await setDeployment(deployment, true);
        onComplete(deployment);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
        setPhase("error");
      }
    },
    [onComplete],
  );

  useEffect(() => {
    if (initialBackendUrl && initialBackendUrl.length > 0) {
      void startLogin(initialBackendUrl);
    }
  }, [initialBackendUrl, startLogin]);

  useInput((input, key) => {
    if (phase === "error") {
      if (input === "r" || input === "R") {
        void startLogin(backendUrl);
        return;
      }
      if (input === "q" || input === "Q" || key.escape) {
        exit();
        return;
      }
    }
  });

  return (
    <Box flexDirection="column" paddingY={1}>
      <Box
        borderStyle="round"
        borderColor={brand}
        paddingX={2}
        flexDirection="column"
      >
        <Box>
          <Text color={brand}>{ARROW_RIGHT_THIN}_ </Text>
          <Text bold>Welcome to Open SWE CLI</Text>
        </Box>
        <Text color={subtle}>
          Sign in with GitHub to use a cloud deployment.
        </Text>
      </Box>

      {phase === "input" ? (
        <Box flexDirection="column" marginTop={1}>
          <Text color={subtle}>Enter the backend URL of your deployment:</Text>
          <Box
            borderStyle="round"
            borderColor={themeColor("promptBorder")}
            paddingX={1}
            marginTop={1}
          >
            <Text color={brand} bold>
              {ARROW_RIGHT_THIN}{" "}
            </Text>
            <Box flexGrow={1}>
              <TextInput
                value={backendUrl}
                onChange={setBackendUrl}
                onSubmit={(v) => void startLogin(v)}
                cursorOffset={cursorOffset}
                onChangeCursorOffset={setCursorOffset}
                onExit={exit}
                placeholder="https://open-swe.example.com"
                multiline={false}
                showCursor={true}
                focus={true}
                columns={72}
              />
            </Box>
          </Box>
        </Box>
      ) : null}

      {phase === "running" ? (
        <Box flexDirection="column" marginTop={1}>
          {statusHistory.slice(0, -1).map((line, i) => (
            <Box key={i}>
              <Text color={success}>{CHECK} </Text>
              <Text color={subtle}>{line}</Text>
            </Box>
          ))}
          <Box>
            <Spinner />
            <Text color={suggestion}> {status}</Text>
          </Box>
        </Box>
      ) : null}

      {phase === "done" ? (
        <Box flexDirection="column" marginTop={1}>
          {statusHistory.map((line, i) => (
            <Box key={i}>
              <Text color={success}>{CHECK} </Text>
              <Text color={subtle}>{line}</Text>
            </Box>
          ))}
        </Box>
      ) : null}

      {phase === "error" ? (
        <Box flexDirection="column" marginTop={1}>
          <Box>
            <Text color={errColor}>{CROSS} </Text>
            <Text color={errColor}>{error}</Text>
          </Box>
          <Box marginTop={1}>
            <Text color={subtle}>
              Press{" "}
            </Text>
            <Text bold>r</Text>
            <Text color={subtle}> to retry, </Text>
            <Text bold>q</Text>
            <Text color={subtle}> to quit.</Text>
          </Box>
        </Box>
      ) : null}
    </Box>
  );
};
