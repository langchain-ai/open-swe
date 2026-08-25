import {
  type LangGraphSettings,
  type ModelSelection,
  TextGenerationError,
} from "@openswe/contracts";
import { sanitizeBranchFragment, sanitizeFeatureBranchName } from "@openswe/shared/git";
import { getModelSelectionStringOptionValue } from "@openswe/shared/model";
import { extractJsonObject } from "@openswe/shared/schemaJson";
import * as Effect from "effect/Effect";
import * as Option from "effect/Option";
import * as Schema from "effect/Schema";
import { HttpClient, HttpClientRequest } from "effect/unstable/http";

import { langGraphAuthHeaders, langGraphBaseUrl } from "../provider/Layers/LangGraphProvider.ts";
import * as TextGeneration from "./TextGeneration.ts";
import {
  buildBranchNamePrompt,
  buildCommitMessagePrompt,
  buildPrContentPrompt,
  buildThreadTitlePrompt,
} from "./TextGenerationPrompts.ts";
import {
  sanitizeCommitSubject,
  sanitizePrTitle,
  sanitizeThreadTitle,
} from "./TextGenerationUtils.ts";

const LANGGRAPH_TIMEOUT_MS = 180_000;

type LangGraphTextGenerationOperation =
  | "generateCommitMessage"
  | "generatePrContent"
  | "generateBranchName"
  | "generateThreadTitle";

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function extractMessageText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((block) => {
      if (typeof block === "string") return block;
      const record = asRecord(block);
      return record?.["type"] === "text" && typeof record["text"] === "string"
        ? record["text"]
        : "";
    })
    .join("");
}

function extractLastAssistantText(value: unknown): string {
  const messages = asRecord(value)?.["messages"];
  if (!Array.isArray(messages)) return "";

  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = asRecord(messages[index]);
    const type = message?.["type"];
    if (
      type !== "ai" &&
      type !== "AIMessage" &&
      type !== "AIMessageChunk" &&
      message?.["role"] !== "assistant"
    ) {
      continue;
    }
    const text = extractMessageText(message?.["content"]).trim();
    if (text.length > 0) return text;
  }
  return "";
}

export const makeLangGraphTextGeneration = Effect.fn("makeLangGraphTextGeneration")(function* (
  settings: LangGraphSettings,
  environment?: NodeJS.ProcessEnv,
) {
  const httpClient = yield* HttpClient.HttpClient;
  const baseUrl = langGraphBaseUrl(settings);
  const headers = langGraphAuthHeaders(settings, environment);

  const fail = (operation: LangGraphTextGenerationOperation, detail: string, cause?: unknown) =>
    new TextGenerationError({
      operation,
      detail,
      ...(cause === undefined ? {} : { cause }),
    });

  const request = (
    operation: LangGraphTextGenerationOperation,
    requestValue: HttpClientRequest.HttpClientRequest,
  ) =>
    httpClient.execute(HttpClientRequest.setHeaders(headers)(requestValue)).pipe(
      Effect.mapError((cause) =>
        fail(operation, `Could not reach the LangGraph server at ${baseUrl}.`, cause),
      ),
      Effect.filterOrFail(
        (response) => response.status >= 200 && response.status < 300,
        (response) =>
          fail(
            operation,
            response.status === 401 || response.status === 403
              ? "The LangGraph server rejected the configured API key."
              : `The LangGraph server answered ${String(response.status)}.`,
          ),
      ),
    );

  const requestJson = (
    operation: LangGraphTextGenerationOperation,
    requestValue: HttpClientRequest.HttpClientRequest,
  ) =>
    request(operation, requestValue).pipe(
      Effect.flatMap((response) =>
        response.json.pipe(
          Effect.mapError((cause) =>
            fail(operation, "The LangGraph server returned a malformed response.", cause),
          ),
        ),
      ),
    );

  const deleteThread = (threadId: string): Effect.Effect<void, never> =>
    httpClient
      .execute(
        HttpClientRequest.delete(`${baseUrl}/threads/${encodeURIComponent(threadId)}`).pipe(
          HttpClientRequest.setHeaders(headers),
        ),
      )
      .pipe(
        Effect.asVoid,
        Effect.catch(() => Effect.void),
      );

  const runLangGraphJson = Effect.fn("runLangGraphJson")(function* <S extends Schema.Top>({
    operation,
    cwd,
    prompt,
    outputSchema,
    modelSelection,
  }: {
    operation: LangGraphTextGenerationOperation;
    cwd: string;
    prompt: string;
    outputSchema: S;
    modelSelection: ModelSelection;
  }): Effect.fn.Return<S["Type"], TextGenerationError, S["DecodingServices"]> {
    if (baseUrl.length === 0) {
      return yield* fail(operation, "Set a server URL for Open SWE in Settings.");
    }

    const created = yield* requestJson(
      operation,
      HttpClientRequest.post(`${baseUrl}/threads`).pipe(
        HttpClientRequest.bodyJsonUnsafe({ metadata: { source: "desktop-text-generation" } }),
      ),
    );
    const threadId = asRecord(created)?.["thread_id"];
    if (typeof threadId !== "string" || threadId.length === 0) {
      return yield* fail(operation, "The LangGraph server did not return a thread id.");
    }

    const effort = getModelSelectionStringOptionValue(modelSelection, "effort");
    const response = yield* requestJson(
      operation,
      HttpClientRequest.post(`${baseUrl}/threads/${encodeURIComponent(threadId)}/runs/wait`).pipe(
        HttpClientRequest.bodyJsonUnsafe({
          assistant_id: settings.graphId,
          input: { messages: [{ role: "user", content: prompt }] },
          config: {
            configurable: {
              source: "desktop",
              local_project_path: cwd,
              agent_model_id: modelSelection.model,
              ...(effort === undefined ? {} : { agent_effort: effort }),
              plan_mode: false,
              runtime_mode: "approval-required",
            },
          },
        }),
      ),
    ).pipe(
      Effect.timeoutOption(LANGGRAPH_TIMEOUT_MS),
      Effect.flatMap(
        Option.match({
          onNone: () => Effect.fail(fail(operation, "LangGraph text generation timed out.")),
          onSome: Effect.succeed,
        }),
      ),
      Effect.ensuring(deleteThread(threadId)),
    );

    const rawOutput = extractLastAssistantText(response);
    if (rawOutput.length === 0) {
      return yield* fail(operation, "LangGraph returned empty text generation output.");
    }

    const decodeOutput = Schema.decodeEffect(Schema.fromJsonString(outputSchema));
    return yield* decodeOutput(extractJsonObject(rawOutput)).pipe(
      Effect.catchTags({
        SchemaError: (cause) =>
          Effect.fail(fail(operation, "LangGraph returned invalid structured output.", cause)),
      }),
    );
  });

  const generateCommitMessage: TextGeneration.TextGeneration["Service"]["generateCommitMessage"] =
    Effect.fn("LangGraphTextGeneration.generateCommitMessage")(function* (input) {
      const { prompt, outputSchema } = buildCommitMessagePrompt({
        branch: input.branch,
        stagedSummary: input.stagedSummary,
        stagedPatch: input.stagedPatch,
        includeBranch: input.includeBranch === true,
        policy: input.policy,
      });
      const generated = yield* runLangGraphJson({
        operation: "generateCommitMessage",
        cwd: input.cwd,
        prompt,
        outputSchema,
        modelSelection: input.modelSelection,
      });
      return {
        subject: sanitizeCommitSubject(generated.subject),
        body: generated.body.trim(),
        ...("branch" in generated && typeof generated.branch === "string"
          ? { branch: sanitizeFeatureBranchName(generated.branch) }
          : {}),
      };
    });

  const generatePrContent: TextGeneration.TextGeneration["Service"]["generatePrContent"] =
    Effect.fn("LangGraphTextGeneration.generatePrContent")(function* (input) {
      const { prompt, outputSchema } = buildPrContentPrompt({
        baseBranch: input.baseBranch,
        headBranch: input.headBranch,
        commitSummary: input.commitSummary,
        diffSummary: input.diffSummary,
        diffPatch: input.diffPatch,
        policy: input.policy,
        changeRequestTemplate: input.changeRequestTemplate,
      });
      const generated = yield* runLangGraphJson({
        operation: "generatePrContent",
        cwd: input.cwd,
        prompt,
        outputSchema,
        modelSelection: input.modelSelection,
      });
      return { title: sanitizePrTitle(generated.title), body: generated.body.trim() };
    });

  const generateBranchName: TextGeneration.TextGeneration["Service"]["generateBranchName"] =
    Effect.fn("LangGraphTextGeneration.generateBranchName")(function* (input) {
      const { prompt, outputSchema } = buildBranchNamePrompt({
        message: input.message,
        attachments: input.attachments,
      });
      const generated = yield* runLangGraphJson({
        operation: "generateBranchName",
        cwd: input.cwd,
        prompt,
        outputSchema,
        modelSelection: input.modelSelection,
      });
      return { branch: sanitizeBranchFragment(generated.branch) };
    });

  const generateThreadTitle: TextGeneration.TextGeneration["Service"]["generateThreadTitle"] =
    Effect.fn("LangGraphTextGeneration.generateThreadTitle")(function* (input) {
      const { prompt, outputSchema } = buildThreadTitlePrompt({
        message: input.message,
        previousTitle: input.previousTitle,
        attachments: input.attachments,
      });
      const generated = yield* runLangGraphJson({
        operation: "generateThreadTitle",
        cwd: input.cwd,
        prompt,
        outputSchema,
        modelSelection: input.modelSelection,
      });
      return { title: sanitizeThreadTitle(generated.title) };
    });

  return {
    generateCommitMessage,
    generatePrContent,
    generateBranchName,
    generateThreadTitle,
  } satisfies TextGeneration.TextGeneration["Service"];
});
