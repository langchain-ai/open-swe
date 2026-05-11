import React, { useCallback, useState } from "react";
import { Box, Text, useInput } from "ink";
import { TextInput } from "./components/TextInput/index.js";
import { Spinner } from "./components/Spinner.js";
import { themeColor } from "./theme.js";
import { ARROW_RIGHT_THIN, CROSS } from "./figures.js";
import type { ApiClient } from "@lib/api-client";

type Field = "repo" | "branch" | "prompt";
type Phase = "input" | "submitting" | "error";

type Props = {
  api: ApiClient;
  initialRepo?: string;
  initialBranch?: string;
  initialPrompt?: string;
  model?: string;
  agent?: string;
  onCreated: (thread_id: string) => void;
  onCancel: () => void;
};

const FIELD_ORDER: Field[] = ["repo", "branch", "prompt"];

export const NewCloud = ({
  api,
  initialRepo,
  initialBranch,
  initialPrompt,
  model,
  agent,
  onCreated,
  onCancel,
}: Props) => {
  const brand = themeColor("brand");
  const subtle = themeColor("subtle");
  const errColor = themeColor("error");
  const promptBorder = themeColor("promptBorder");

  const [repo, setRepo] = useState<string>(initialRepo ?? "");
  const [repoCursor, setRepoCursor] = useState<number>((initialRepo ?? "").length);
  const [branch, setBranch] = useState<string>(initialBranch ?? "main");
  const [branchCursor, setBranchCursor] = useState<number>(
    (initialBranch ?? "main").length,
  );
  const [prompt, setPrompt] = useState<string>(initialPrompt ?? "");
  const [promptCursor, setPromptCursor] = useState<number>(
    (initialPrompt ?? "").length,
  );

  const [focused, setFocused] = useState<Field>(() => {
    if (!initialRepo) return "repo";
    if (!initialBranch) return "branch";
    return "prompt";
  });
  const [phase, setPhase] = useState<Phase>("input");
  const [error, setError] = useState<string>("");

  const submit = useCallback(async () => {
    if (!repo.trim() || !branch.trim() || !prompt.trim()) {
      setError("All fields are required.");
      setPhase("error");
      return;
    }
    setPhase("submitting");
    setError("");
    try {
      const res = await api.createRun({
        repo: repo.trim(),
        branch: branch.trim(),
        prompt,
        model,
        agent,
      });
      onCreated(res.thread_id);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      setPhase("error");
    }
  }, [api, repo, branch, prompt, model, agent, onCreated]);

  const cycle = useCallback(
    (dir: 1 | -1) => {
      const idx = FIELD_ORDER.indexOf(focused);
      const next =
        FIELD_ORDER[(idx + dir + FIELD_ORDER.length) % FIELD_ORDER.length];
      setFocused(next!);
    },
    [focused],
  );

  useInput((input, key) => {
    if (phase === "submitting") return;
    if (key.escape) {
      onCancel();
      return;
    }
    if (key.tab) {
      cycle(key.shift ? -1 : 1);
      return;
    }
    if (phase === "error") {
      if (input === "r" || input === "R") {
        setPhase("input");
        setError("");
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
        marginBottom={1}
      >
        <Box>
          <Text color={brand}>{ARROW_RIGHT_THIN}_ </Text>
          <Text bold>New cloud run</Text>
        </Box>
        <Text color={subtle}>
          Tab to switch fields  ·  Enter on prompt to submit  ·  Esc to cancel
        </Text>
      </Box>

      <Field
        label="repo"
        focused={focused === "repo"}
        borderColor={focused === "repo" ? brand : promptBorder}
      >
        <TextInput
          value={repo}
          onChange={setRepo}
          onSubmit={() => setFocused("branch")}
          cursorOffset={repoCursor}
          onChangeCursorOffset={setRepoCursor}
          placeholder="owner/repo (e.g. langchain-ai/open-swe)"
          multiline={false}
          showCursor={focused === "repo"}
          focus={focused === "repo" && phase !== "submitting"}
          columns={72}
        />
      </Field>

      <Field
        label="branch"
        focused={focused === "branch"}
        borderColor={focused === "branch" ? brand : promptBorder}
      >
        <TextInput
          value={branch}
          onChange={setBranch}
          onSubmit={() => setFocused("prompt")}
          cursorOffset={branchCursor}
          onChangeCursorOffset={setBranchCursor}
          placeholder="main"
          multiline={false}
          showCursor={focused === "branch"}
          focus={focused === "branch" && phase !== "submitting"}
          columns={72}
        />
      </Field>

      <Field
        label="prompt"
        focused={focused === "prompt"}
        borderColor={focused === "prompt" ? brand : promptBorder}
      >
        <TextInput
          value={prompt}
          onChange={setPrompt}
          onSubmit={() => void submit()}
          cursorOffset={promptCursor}
          onChangeCursorOffset={setPromptCursor}
          placeholder="Describe what you want Open SWE to do…"
          multiline={true}
          showCursor={focused === "prompt"}
          focus={focused === "prompt" && phase !== "submitting"}
          columns={72}
        />
      </Field>

      {phase === "submitting" ? (
        <Box marginTop={1}>
          <Spinner />
          <Text color={subtle}> Creating cloud run…</Text>
        </Box>
      ) : null}

      {phase === "error" ? (
        <Box flexDirection="column" marginTop={1}>
          <Box>
            <Text color={errColor}>{CROSS} </Text>
            <Text color={errColor}>{error}</Text>
          </Box>
          <Box marginTop={1}>
            <Text color={subtle}>Press </Text>
            <Text bold>r</Text>
            <Text color={subtle}> to retry, </Text>
            <Text bold>Esc</Text>
            <Text color={subtle}> to cancel.</Text>
          </Box>
        </Box>
      ) : null}
    </Box>
  );
};

type FieldProps = {
  label: string;
  focused: boolean;
  borderColor: string;
  children: React.ReactNode;
};

const Field = ({ label, focused, borderColor, children }: FieldProps) => {
  const subtle = themeColor("subtle");
  const brand = themeColor("brand");
  return (
    <Box flexDirection="column" marginBottom={1}>
      <Box>
        <Text color={focused ? brand : subtle} bold={focused}>
          {label}
        </Text>
      </Box>
      <Box
        borderStyle="round"
        borderColor={borderColor}
        paddingX={1}
      >
        <Box flexGrow={1}>{children}</Box>
      </Box>
    </Box>
  );
};
