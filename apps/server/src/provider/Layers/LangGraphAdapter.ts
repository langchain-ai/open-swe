/**
 * LangGraphAdapter — provider-runtime adapter for the Open SWE agent.
 *
 * Drives a LangGraph server over its HTTP API and translates the run's SSE
 * stream into canonical `ProviderRuntimeEvent`s:
 *
 *   startSession   -> POST {baseUrl}/threads
 *   sendTurn       -> POST {baseUrl}/threads/{id}/runs/stream
 *   interruptTurn  -> POST {baseUrl}/threads/{id}/runs/{runId}/cancel
 *
 * The agent runs against the user's real checkout rather than a cloud
 * sandbox: `configurable.source = "desktop"` selects that path in
 * `agent/server.py`, and `local_project_path` names the project. The path is
 * a request, not a grant — `agent/desktop.py:resolve_desktop_project`
 * realpath-checks it against the allowlist at `$OPEN_SWE_LOCAL_PROJECTS_FILE`
 * and rejects anything outside it.
 *
 * @module provider/Layers/LangGraphAdapter
 */
import {
  ApprovalRequestId,
  type ChatAttachment,
  type CanonicalItemType,
  type CanonicalRequestType,
  EventId,
  type LangGraphSettings,
  type ProviderApprovalDecision,
  type ProviderUserInputAnswers,
  ProviderDriverKind,
  type ProviderInstanceId,
  type ProviderRuntimeEvent,
  type RuntimeEventRaw,
  type ProviderSession,
  isProviderSendTurnSupportedImageMimeType,
  RuntimeItemId,
  RuntimeRequestId,
  RuntimeTaskId,
  type ThreadId,
  TurnId,
  type UserInputQuestion,
} from "@openswe/contracts";
import { getModelSelectionStringOptionValue } from "@openswe/shared/model";
import * as Cause from "effect/Cause";
import * as Clock from "effect/Clock";
import * as DateTime from "effect/DateTime";
import * as Effect from "effect/Effect";
import * as Exit from "effect/Exit";
import * as FileSystem from "effect/FileSystem";
import * as Fiber from "effect/Fiber";
import * as Path from "effect/Path";
import * as PubSub from "effect/PubSub";
import * as Result from "effect/Result";
import * as Scope from "effect/Scope";
import * as Schema from "effect/Schema";
import * as Semaphore from "effect/Semaphore";
import * as Stream from "effect/Stream";
import { HttpClient, HttpClientRequest } from "effect/unstable/http";

import {
  type ProviderAdapterError,
  ProviderAdapterRequestError,
  ProviderAdapterValidationError,
} from "../Errors.ts";
import { resolveAttachmentPath } from "../../attachmentStore.ts";
import { writeFileStringAtomically } from "../../atomicWrite.ts";
import type { ProviderAdapterShape } from "../Services/ProviderAdapter.ts";
import { langGraphAuthHeaders, langGraphBaseUrl } from "./LangGraphProvider.ts";
import type { EventNdjsonLogger } from "./EventNdjsonLogger.ts";

const DRIVER_KIND = ProviderDriverKind.make("langgraph");
const LANGGRAPH_STREAM_MODES = [
  "messages",
  "messages-tuple",
  "updates",
  "custom",
  "tasks",
  "checkpoints",
] as const;
const MAX_TRACKED_TURN_ENTRIES = 512;
const MAX_CHUNKS_PER_ITEM = 256;
const MAX_RUN_STREAM_RECONNECTS = 2;
const MAX_SSE_EVENT_ID_LENGTH = 1_024;
const MAX_SSE_FRAME_DATA_LENGTH = 1_048_576;
const MAX_DIAGNOSTIC_STRING_LENGTH = 4_096;
const MAX_DIAGNOSTIC_ENTRIES = 1_000;
const MAX_PENDING_INTERRUPT_ENTRIES = 100;
const MAX_INTERRUPT_ACTIONS = 32;
const MAX_USER_INPUT_QUESTIONS = 16;
const MAX_USER_INPUT_OPTIONS = 16;
const decodeUnknownJsonStringExit = Schema.decodeUnknownExit(Schema.fromJsonString(Schema.Unknown));
const encodeUnknownJsonStringExit = Schema.encodeUnknownExit(Schema.fromJsonString(Schema.Unknown));

interface LangGraphSessionState {
  readonly langGraphThreadId: string;
  readonly createdAt: string;
  readonly cwd: string | undefined;
  readonly runtimeMode: ProviderSession["runtimeMode"];
  model: string | undefined;
  effort: string | undefined;
  planMode: boolean;
  activeTurnId: TurnId | undefined;
  activeRunId: string | undefined;
  activeRunGeneration: number;
  activeFiber: Fiber.Fiber<void, never> | undefined;
  activeRunConfig: Record<string, unknown> | undefined;
  activeEventState: LangGraphTurnEventState | undefined;
  readonly pendingTurns: Array<LangGraphPendingTurn>;
  readonly pendingApprovals: Map<string, LangGraphPendingApproval>;
  readonly approvalDecisionGroups: Map<string, Array<LangGraphHitlDecision | undefined>>;
  readonly pendingUserInputs: Map<string, LangGraphPendingUserInput>;
  readonly resolvedInterrupts: Map<string, unknown>;
  lastError: string | undefined;
}

interface LangGraphPendingTurn {
  readonly threadId: ThreadId;
  readonly turnId: TurnId;
  readonly body: Record<string, unknown>;
  readonly model: string | undefined;
  readonly effort: string | undefined;
}

interface LangGraphActionRequest {
  readonly name: string;
  readonly args: Record<string, unknown>;
  readonly description?: string;
  readonly allowedDecisions: ReadonlyArray<LangGraphReviewDecision>;
}

interface LangGraphInterrupt {
  readonly id: string;
  readonly actions: ReadonlyArray<LangGraphActionRequest>;
}

interface LangGraphPendingApproval {
  readonly interruptId: string;
  readonly actionIndex: number;
  readonly actionCount: number;
  readonly action: LangGraphActionRequest;
  readonly requestId: ApprovalRequestId;
  readonly requestType: CanonicalRequestType;
}

interface LangGraphPendingUserInput {
  readonly id: string;
  readonly requestId: ApprovalRequestId;
  readonly questions: ReadonlyArray<UserInputQuestion>;
}

type LangGraphReviewDecision = "approve" | "edit" | "reject" | "respond";

type LangGraphHitlDecision =
  | { readonly type: "approve" }
  | { readonly type: "reject"; readonly message?: string };

interface LangGraphHitlResponse {
  readonly decisions: ReadonlyArray<LangGraphHitlDecision>;
}

interface LangGraphTurnEventState {
  readonly emittedText: Map<string, number>;
  readonly assistantItems: Set<string>;
  readonly startedTools: Set<string>;
  readonly completedTools: Set<string>;
  readonly emittedUsage: Set<string>;
  readonly chunkHistory: Map<string, Array<string>>;
  readonly reasoningItems: Set<string>;
  readonly tasks: Map<string, LangGraphTaskState>;
  readonly toolNames: Map<string, string>;
  readonly namespaceTasks: Map<string, string>;
  readonly protocolMessages: Map<string, string>;
  lastPlan: string | undefined;
  lastDiff: string | undefined;
  failure: string | undefined;
}

interface LangGraphTaskState {
  readonly description?: string;
  readonly taskType?: string;
  completed: boolean;
}

interface LangGraphResumeCursor {
  readonly threadId: string;
}

interface LangGraphAdapterDependencies {
  readonly attachmentsDir?: string;
  readonly environment?: NodeJS.ProcessEnv;
  readonly nativeEventLogger?: EventNdjsonLogger;
}

function boundedDiagnosticValue(value: unknown): unknown {
  let remaining = MAX_DIAGNOSTIC_ENTRIES;
  const visit = (candidate: unknown, depth: number): unknown => {
    remaining -= 1;
    if (remaining < 0) return "[truncated]";
    if (typeof candidate === "string") return candidate.slice(0, MAX_DIAGNOSTIC_STRING_LENGTH);
    if (candidate === null || typeof candidate === "boolean" || typeof candidate === "number") {
      return candidate;
    }
    if (depth >= 8) return "[truncated]";
    if (Array.isArray(candidate)) {
      return candidate
        .slice(0, Math.min(candidate.length, remaining))
        .map((entry) => visit(entry, depth + 1));
    }
    const record = asRecord(candidate);
    if (record === undefined) return String(candidate).slice(0, MAX_DIAGNOSTIC_STRING_LENGTH);
    const entries = Object.entries(record).slice(
      0,
      Math.min(Object.keys(record).length, remaining),
    );
    return Object.fromEntries(
      entries.map(([key, entry]) => [
        key.slice(0, MAX_DIAGNOSTIC_STRING_LENGTH),
        visit(entry, depth + 1),
      ]),
    );
  };
  return visit(value, 0);
}

function readResumeCursor(value: unknown): LangGraphResumeCursor | undefined {
  const threadId = asRecord(value)?.["threadId"];
  return typeof threadId === "string" && threadId.length > 0 ? { threadId } : undefined;
}

function isLoopbackServer(baseUrl: string): boolean {
  try {
    const url = new URL(baseUrl);
    if (url.protocol !== "http:" && url.protocol !== "https:") return false;
    const hostname = url.hostname.toLowerCase().replace(/^\[|\]$/g, "");
    return hostname === "localhost" || hostname === "::1" || /^127(?:\.\d{1,3}){3}$/.test(hostname);
  } catch {
    return false;
  }
}

/**
 * Deepagents' tool surface, mapped onto the canonical item types the
 * timeline knows how to render. Anything unrecognised stays a generic tool
 * call rather than being forced into a shape it does not fit.
 */
function classifyToolItemType(toolName: string): CanonicalItemType {
  const name = toolName.toLowerCase();
  if (name === "task") return "collab_agent_tool_call";
  if (name === "execute" || name.includes("bash") || name.includes("shell")) {
    return "command_execution";
  }
  if (name === "write_file" || name === "edit_file" || name === "delete") return "file_change";
  if (name.includes("web_search")) return "web_search";
  if (name.startsWith("mcp")) return "mcp_tool_call";
  return "dynamic_tool_call";
}

function classifyRequestType(actions: ReadonlyArray<LangGraphActionRequest>): CanonicalRequestType {
  const itemTypes = new Set(actions.map((action) => classifyToolItemType(action.name)));
  if (itemTypes.size !== 1) return "dynamic_tool_call";
  const itemType = itemTypes.values().next().value;
  if (itemType === "command_execution") return "command_execution_approval";
  if (itemType === "file_change") return "file_change_approval";
  if (itemType === "mcp_tool_call") return "mcp_elicitation_approval";
  return "dynamic_tool_call";
}

type LangGraphParsedInterrupt =
  | { readonly kind: "approval"; readonly interrupt: LangGraphInterrupt }
  | { readonly kind: "userInput"; readonly interrupt: LangGraphPendingUserInput };

function readNonEmptyString(
  value: unknown,
  maxLength = MAX_DIAGNOSTIC_STRING_LENGTH,
): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed.length > 0 && trimmed.length <= maxLength ? trimmed : undefined;
}

function readUserInputQuestions(value: unknown): ReadonlyArray<UserInputQuestion> | undefined {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_USER_INPUT_QUESTIONS) {
    return undefined;
  }
  const questions: Array<UserInputQuestion> = [];
  const ids = new Set<string>();
  for (const rawQuestion of value) {
    const question = asRecord(rawQuestion);
    const text = readNonEmptyString(question?.["question"]);
    const header = readNonEmptyString(question?.["header"]);
    const id = readNonEmptyString(question?.["id"]) ?? text;
    const rawOptions = question?.["options"];
    const rawMultiSelect = question?.["multiSelect"] ?? question?.["multi_select"];
    if (
      question === undefined ||
      text === undefined ||
      header === undefined ||
      id === undefined ||
      ids.has(id) ||
      !Array.isArray(rawOptions) ||
      rawOptions.length > MAX_USER_INPUT_OPTIONS ||
      (rawMultiSelect !== undefined && typeof rawMultiSelect !== "boolean")
    ) {
      return undefined;
    }
    const options: Array<{ label: string; description: string }> = [];
    for (const rawOption of rawOptions) {
      const option = asRecord(rawOption);
      const label = readNonEmptyString(option?.["label"]);
      const description = readNonEmptyString(option?.["description"]);
      if (option === undefined || label === undefined || description === undefined) {
        return undefined;
      }
      options.push({ label, description });
    }
    ids.add(id);
    questions.push({
      id,
      header,
      question: text,
      options,
      multiSelect: rawMultiSelect ?? false,
    });
  }
  return questions;
}

function readInterrupts(value: unknown): ReadonlyArray<LangGraphParsedInterrupt> | undefined {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_PENDING_INTERRUPT_ENTRIES) {
    return undefined;
  }
  const interrupts: Array<LangGraphParsedInterrupt> = [];
  const ids = new Set<string>();
  for (const rawInterrupt of value) {
    const interrupt = asRecord(rawInterrupt);
    const id = readNonEmptyString(interrupt?.["id"]);
    const interruptValue = asRecord(interrupt?.["value"]);
    if (id === undefined || ids.has(id) || interruptValue === undefined) return undefined;
    ids.add(id);

    if ("questions" in interruptValue) {
      if ("action_requests" in interruptValue) return undefined;
      const questions = readUserInputQuestions(interruptValue["questions"]);
      if (questions === undefined) return undefined;
      interrupts.push({
        kind: "userInput",
        interrupt: { id, requestId: ApprovalRequestId.make(id), questions },
      });
      continue;
    }

    const actionRequests = interruptValue["action_requests"];
    if (
      !Array.isArray(actionRequests) ||
      actionRequests.length === 0 ||
      actionRequests.length > MAX_INTERRUPT_ACTIONS
    )
      return undefined;
    const actions: Array<Omit<LangGraphActionRequest, "allowedDecisions">> = [];
    for (const rawAction of actionRequests) {
      const action = asRecord(rawAction);
      const name = readNonEmptyString(action?.["name"]);
      const args = asRecord(boundedDiagnosticValue(action?.["args"]));
      const description = readNonEmptyString(action?.["description"]);
      if (name === undefined || args === undefined) return undefined;
      actions.push({
        name,
        args,
        ...(description === undefined ? {} : { description }),
      });
    }
    if (actions.length === 0) return undefined;

    const rawReviewConfigs = interruptValue["review_configs"];
    let allowedDecisions: Array<ReadonlyArray<LangGraphReviewDecision>>;
    if (rawReviewConfigs === undefined) {
      allowedDecisions = actions.map(() => ["approve", "reject"]);
    } else {
      if (!Array.isArray(rawReviewConfigs) || rawReviewConfigs.length !== actions.length) {
        return undefined;
      }
      allowedDecisions = [];
      for (const [index, rawConfig] of rawReviewConfigs.entries()) {
        const config = asRecord(rawConfig);
        const actionName = readNonEmptyString(config?.["action_name"]);
        const rawAllowed = config?.["allowed_decisions"];
        if (actionName !== actions[index]?.name || !Array.isArray(rawAllowed)) return undefined;
        const decisions: LangGraphReviewDecision[] = [];
        for (const rawDecision of rawAllowed) {
          if (
            rawDecision !== "approve" &&
            rawDecision !== "edit" &&
            rawDecision !== "reject" &&
            rawDecision !== "respond"
          ) {
            return undefined;
          }
          if (!decisions.includes(rawDecision)) decisions.push(rawDecision);
        }
        if (decisions.length === 0) return undefined;
        allowedDecisions.push(decisions);
      }
    }
    interrupts.push({
      kind: "approval",
      interrupt: {
        id,
        actions: actions.map((action, index) => ({
          ...action,
          allowedDecisions: allowedDecisions[index]!,
        })),
      },
    });
  }
  return interrupts;
}

function hitlDecision(
  action: LangGraphActionRequest,
  decision: ProviderApprovalDecision,
): LangGraphHitlDecision | undefined {
  if (decision === "accept") {
    return action.allowedDecisions.includes("approve") ? { type: "approve" } : undefined;
  }
  if (decision === "decline" || decision === "cancel") {
    return action.allowedDecisions.includes("reject")
      ? {
          type: "reject",
          message:
            decision === "cancel"
              ? "User cancelled tool execution."
              : "User declined tool execution.",
        }
      : undefined;
  }
  return undefined;
}

function titleForItemType(itemType: CanonicalItemType, toolName: string): string {
  switch (itemType) {
    case "command_execution":
      return "Command run";
    case "file_change":
      return "File change";
    case "collab_agent_tool_call":
      return "Subagent task";
    case "web_search":
      return "Web search";
    case "mcp_tool_call":
      return "MCP tool call";
    default:
      return toolName;
  }
}

/**
 * LangGraph message content is either a plain string or a list of typed
 * blocks; only the text blocks carry renderable content.
 */
function extractText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  let text = "";
  for (const block of content) {
    if (typeof block === "string") {
      text += block;
      continue;
    }
    if (block !== null && typeof block === "object") {
      const record = block as Record<string, unknown>;
      if (record["type"] === "text" && typeof record["text"] === "string") {
        text += record["text"];
      }
    }
  }
  return text;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function addBounded(set: Set<string>, value: string): void {
  if (set.has(value)) return;
  if (set.size >= MAX_TRACKED_TURN_ENTRIES) {
    const oldest = set.values().next().value;
    if (typeof oldest === "string") set.delete(oldest);
  }
  set.add(value);
}

function setBounded<T>(map: Map<string, T>, key: string, value: T): void {
  if (!map.has(key) && map.size >= MAX_TRACKED_TURN_ENTRIES) {
    const oldest = map.keys().next().value;
    if (typeof oldest === "string") map.delete(oldest);
  }
  map.set(key, value);
}

function parseNamespacedEvent(
  value: string,
): { readonly event: string; readonly namespace: ReadonlyArray<string> } | undefined {
  const [event, ...namespace] = value.split("|");
  if (event === undefined || event.length === 0 || namespace.some((part) => part.length === 0)) {
    return undefined;
  }
  return { event, namespace };
}

function reasoningText(content: unknown): string {
  if (!Array.isArray(content)) return "";
  let text = "";
  for (const block of content) {
    const record = asRecord(block);
    if (record === undefined) continue;
    const type = record["type"];
    if (type !== "reasoning" && type !== "thinking") continue;
    const value = record["reasoning"] ?? record["thinking"] ?? record["text"];
    if (typeof value === "string") text += value;
  }
  return text;
}

function readPlanSteps(value: unknown) {
  const todos = asRecord(value)?.["todos"];
  if (!Array.isArray(todos) || todos.length === 0) return undefined;
  const plan = todos.flatMap((rawTodo) => {
    const todo = asRecord(rawTodo);
    const label = todo?.["content"] ?? todo?.["step"] ?? todo?.["subject"];
    if (typeof label !== "string" || label.trim().length === 0) return [];
    const rawStatus = todo?.["status"];
    const status =
      rawStatus === "completed"
        ? ("completed" as const)
        : rawStatus === "in_progress" || rawStatus === "inProgress" || rawStatus === "running"
          ? ("inProgress" as const)
          : ("pending" as const);
    return [{ step: label.trim(), status }];
  });
  return plan.length === 0 ? undefined : plan;
}

function findExplicitDiff(value: unknown, depth = 0): string | undefined {
  if (depth > 3) return undefined;
  const record = asRecord(value);
  if (record === undefined) return undefined;
  for (const key of ["unifiedDiff", "unified_diff", "diff"] as const) {
    const diff = record[key];
    if (typeof diff === "string") return diff;
  }
  for (const nested of Object.values(record)) {
    const diff = findExplicitDiff(nested, depth + 1);
    if (diff !== undefined) return diff;
  }
  return undefined;
}

function summarize(value: unknown, max = 400): string | undefined {
  if (value === undefined || value === null) return undefined;
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (typeof text !== "string" || text.length === 0) return undefined;
  return text.length <= max ? text : `${text.slice(0, max - 3)}...`;
}

function nonNegativeInteger(value: unknown): number | undefined {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : undefined;
}

function readUsage(message: Record<string, unknown>) {
  const usage = asRecord(message["usage_metadata"]);
  if (usage === undefined) return undefined;
  const inputTokens = nonNegativeInteger(usage["input_tokens"]);
  const outputTokens = nonNegativeInteger(usage["output_tokens"]);
  const totalTokens =
    nonNegativeInteger(usage["total_tokens"]) ??
    (inputTokens === undefined && outputTokens === undefined
      ? undefined
      : (inputTokens ?? 0) + (outputTokens ?? 0));
  if (totalTokens === undefined) return undefined;
  const inputDetails = asRecord(usage["input_token_details"]);
  const cachedInputTokens = nonNegativeInteger(
    inputDetails?.["cache_read"] ?? inputDetails?.["cache_read_input_tokens"],
  );
  return {
    usedTokens: totalTokens,
    totalProcessedTokens: totalTokens,
    ...(inputTokens === undefined ? {} : { inputTokens }),
    ...(cachedInputTokens === undefined ? {} : { cachedInputTokens }),
    ...(outputTokens === undefined ? {} : { outputTokens }),
    lastUsedTokens: totalTokens,
    ...(inputTokens === undefined ? {} : { lastInputTokens: inputTokens }),
    ...(cachedInputTokens === undefined ? {} : { lastCachedInputTokens: cachedInputTokens }),
    ...(outputTokens === undefined ? {} : { lastOutputTokens: outputTokens }),
  };
}

function snapshotMessages(value: unknown): ReadonlyArray<Record<string, unknown>> | undefined {
  const state = asRecord(value);
  const values = asRecord(state?.["values"]);
  const messages = values?.["messages"];
  if (state === undefined || values === undefined) return undefined;
  if (messages === undefined) return [];
  if (!Array.isArray(messages)) return undefined;
  const parsed: Array<Record<string, unknown>> = [];
  for (const message of messages) {
    const record = asRecord(message);
    const type = record?.["type"];
    if (record === undefined || typeof type !== "string" || type.length === 0) return undefined;
    parsed.push({
      type,
      ...(typeof record["id"] === "string" ? { id: record["id"] } : {}),
      content: record["content"],
      ...(Array.isArray(record["tool_calls"]) ? { tool_calls: record["tool_calls"] } : {}),
      ...(typeof record["tool_call_id"] === "string"
        ? { tool_call_id: record["tool_call_id"] }
        : {}),
      ...(typeof record["name"] === "string" ? { name: record["name"] } : {}),
    });
  }
  return parsed;
}

function snapshotTurns(threadId: ThreadId, messages: ReadonlyArray<Record<string, unknown>>) {
  const turns: Array<{ id: TurnId; items: Array<unknown> }> = [];
  let current: { id: TurnId; items: Array<unknown> } | undefined;
  for (const [index, message] of messages.entries()) {
    if (message["type"] === "human" || message["type"] === "user") {
      current = {
        id: TurnId.make(
          typeof message["id"] === "string"
            ? message["id"]
            : `langgraph-history-turn-${String(index)}`,
        ),
        items: [],
      };
      turns.push(current);
    }
    if (current === undefined) {
      current = {
        id: TurnId.make(`langgraph-history-turn-${String(index)}`),
        items: [],
      };
      turns.push(current);
    }
    current.items.push(message);
  }
  return { threadId, turns };
}

function userTurnCount(messages: ReadonlyArray<Record<string, unknown>>): number {
  return messages.filter((message) => message["type"] === "human" || message["type"] === "user")
    .length;
}

interface LangGraphCheckpoint {
  readonly thread_id: string;
  readonly checkpoint_ns: string;
  readonly checkpoint_id: string;
}

function readCheckpoint(value: unknown, expectedThreadId: string): LangGraphCheckpoint | undefined {
  const checkpoint = asRecord(value);
  const threadId = checkpoint?.["thread_id"];
  const namespace = checkpoint?.["checkpoint_ns"];
  const checkpointId = checkpoint?.["checkpoint_id"];
  return threadId === expectedThreadId &&
    typeof namespace === "string" &&
    typeof checkpointId === "string" &&
    checkpointId.length > 0
    ? { thread_id: threadId, checkpoint_ns: namespace, checkpoint_id: checkpointId }
    : undefined;
}

interface SseEvent {
  readonly event: string;
  readonly data: string;
  readonly id?: string;
  readonly overflow?: boolean;
}

/**
 * Decode an SSE byte stream into `{event, data}` records. Frames are
 * separated by a blank line; `data:` lines within one frame concatenate.
 */
function decodeSse<E, R>(bytes: Stream.Stream<Uint8Array, E, R>): Stream.Stream<SseEvent, E, R> {
  let event = "message";
  let data = "";
  let id: string | undefined;
  let overflow = false;
  return bytes.pipe(
    Stream.decodeText(),
    Stream.splitLines,
    Stream.filterMap((line) => {
      if (line === "") {
        if (data === "" && !overflow) {
          event = "message";
          return Result.failVoid;
        }
        const frame: SseEvent = {
          event,
          data,
          ...(id === undefined ? {} : { id }),
          ...(overflow ? { overflow: true } : {}),
        };
        event = "message";
        data = "";
        overflow = false;
        return Result.succeed(frame);
      }
      if (line.startsWith("event:")) {
        const nextEvent = line.slice(6).trim();
        if (nextEvent.length > MAX_SSE_EVENT_ID_LENGTH) overflow = true;
        else event = nextEvent;
      } else if (line.startsWith("id:")) {
        const nextId = line.slice(3).trim();
        if (!nextId.includes("\0") && nextId.length <= MAX_SSE_EVENT_ID_LENGTH) {
          id = nextId.length === 0 ? undefined : nextId;
        }
      } else if (line.startsWith("data:")) {
        const chunk = line.slice(5).replace(/^ /, "");
        const separatorLength = data === "" ? 0 : 1;
        if (data.length + separatorLength + chunk.length > MAX_SSE_FRAME_DATA_LENGTH) {
          overflow = true;
        } else if (!overflow) {
          data = data === "" ? chunk : `${data}\n${chunk}`;
        }
      }
      return Result.failVoid;
    }),
  );
}

export function makeLangGraphAdapter(
  config: LangGraphSettings,
  providerInstanceId: ProviderInstanceId,
  dependencies: LangGraphAdapterDependencies = {},
): Effect.Effect<
  ProviderAdapterShape<ProviderAdapterError>,
  never,
  FileSystem.FileSystem | HttpClient.HttpClient | Path.Path | Scope.Scope
> {
  return Effect.gen(function* () {
    const httpClient = yield* HttpClient.HttpClient;
    const fileSystem = yield* FileSystem.FileSystem;
    const pathService = yield* Path.Path;
    const providerScope = yield* Scope.Scope;
    const sessions = new Map<ThreadId, LangGraphSessionState>();
    const runtimeEvents = yield* PubSub.unbounded<ProviderRuntimeEvent>();
    const allowlistMutex = yield* Semaphore.make(1);
    const nativeEventLogger = dependencies.nativeEventLogger;

    const baseUrl = langGraphBaseUrl(config);
    const headers = langGraphAuthHeaders(config, dependencies.environment);

    const nowIso = Effect.map(DateTime.now, DateTime.formatIso);
    let eventSeq = 0;
    let nativeEventSeq = 0;
    let turnSeq = 0;
    const nextEventId = (createdAt: string) =>
      EventId.make(`langgraph-${createdAt}-${String(++eventSeq)}`);

    const publishEvent = (
      event: Omit<
        ProviderRuntimeEvent,
        "eventId" | "provider" | "providerInstanceId" | "createdAt"
      >,
    ): Effect.Effect<void> =>
      Effect.gen(function* () {
        const createdAt = yield* nowIso;
        yield* PubSub.publish(runtimeEvents, {
          ...event,
          eventId: nextEventId(createdAt),
          provider: DRIVER_KIND,
          providerInstanceId,
          createdAt,
        } as ProviderRuntimeEvent);
      }).pipe(Effect.asVoid);
    const emit = publishEvent;

    const logNativeFrame = (
      threadId: ThreadId,
      turnId: TurnId,
      frame: SseEvent,
      raw: RuntimeEventRaw,
    ) =>
      Effect.gen(function* () {
        if (!nativeEventLogger) return;
        const observedAt = yield* nowIso;
        yield* nativeEventLogger.write(
          {
            observedAt,
            event: {
              id: `langgraph-native-${observedAt}-${String(++nativeEventSeq)}`,
              kind: "notification",
              provider: DRIVER_KIND,
              providerInstanceId,
              createdAt: observedAt,
              method: frame.event.slice(0, MAX_SSE_EVENT_ID_LENGTH),
              threadId,
              turnId,
              payload: raw.payload,
            },
          },
          threadId,
        );
      }).pipe(
        Effect.catchCause((cause) =>
          Effect.logWarning("Failed to write native LangGraph SSE event log.", {
            cause,
            threadId,
            turnId,
            event: frame.event.slice(0, MAX_SSE_EVENT_ID_LENGTH),
          }),
        ),
      );

    const ensureDesktopProjectAllowed = (cwd: string | undefined) => {
      const allowlistPath = (
        dependencies.environment ?? process.env
      ).OPEN_SWE_LOCAL_PROJECTS_FILE?.trim();
      if (cwd === undefined || allowlistPath === undefined || !isLoopbackServer(baseUrl)) {
        return Effect.void;
      }
      const projectsFile = allowlistPath;

      return allowlistMutex.withPermit(
        Effect.gen(function* () {
          const canonicalCwd = yield* fileSystem
            .realPath(cwd)
            .pipe(
              Effect.mapError((cause) =>
                failRequest(
                  "startSession",
                  "The selected project directory could not be resolved.",
                  cause,
                ),
              ),
            );
          const info = yield* fileSystem
            .stat(canonicalCwd)
            .pipe(
              Effect.mapError((cause) =>
                failRequest(
                  "startSession",
                  "The selected project directory could not be inspected.",
                  cause,
                ),
              ),
            );
          if (info.type !== "Directory") {
            return yield* Effect.fail(
              failRequest("startSession", "The selected project path is not a directory."),
            );
          }

          const contents = yield* fileSystem.readFileString(projectsFile).pipe(
            Effect.matchEffect({
              onFailure: (cause) =>
                cause.reason._tag === "NotFound"
                  ? Effect.succeed("[]")
                  : Effect.fail(
                      failRequest(
                        "startSession",
                        "The Open SWE project allowlist could not be read.",
                        cause,
                      ),
                    ),
              onSuccess: Effect.succeed,
            }),
          );
          const decodedEntries = decodeUnknownJsonStringExit(contents);
          if (Exit.isFailure(decodedEntries) || !Array.isArray(decodedEntries.value)) {
            return yield* Effect.fail(
              failRequest(
                "startSession",
                "The Open SWE project allowlist is not a JSON array.",
                Exit.isFailure(decodedEntries) ? decodedEntries.cause : undefined,
              ),
            );
          }
          const entries = decodedEntries.value;

          const alreadyAllowed = yield* Effect.forEach(entries, (entry) => {
            const candidate =
              typeof entry === "string"
                ? entry
                : typeof asRecord(entry)?.["cwd"] === "string"
                  ? (asRecord(entry)?.["cwd"] as string)
                  : undefined;
            return candidate === undefined
              ? Effect.succeed(false)
              : fileSystem.realPath(candidate).pipe(
                  Effect.map((resolved) => resolved === canonicalCwd),
                  Effect.orElseSucceed(() => false),
                );
          }).pipe(Effect.map((matches) => matches.some(Boolean)));
          if (alreadyAllowed) return;

          const encodedEntries = encodeUnknownJsonStringExit([...entries, canonicalCwd]);
          if (Exit.isFailure(encodedEntries)) {
            return yield* Effect.fail(
              failRequest(
                "startSession",
                "The Open SWE project allowlist could not be encoded.",
                encodedEntries.cause,
              ),
            );
          }
          yield* writeFileStringAtomically({
            filePath: projectsFile,
            contents: `${encodedEntries.value}\n`,
          }).pipe(
            Effect.provideService(FileSystem.FileSystem, fileSystem),
            Effect.provideService(Path.Path, pathService),
            Effect.mapError((cause) =>
              failRequest(
                "startSession",
                "The Open SWE project allowlist could not be updated.",
                cause,
              ),
            ),
          );
        }),
      );
    };

    const failRequest = (method: string, detail: string, cause?: unknown) =>
      new ProviderAdapterRequestError({
        provider: DRIVER_KIND,
        method,
        detail,
        ...(cause === undefined ? {} : { cause }),
      });

    const requireSession = (threadId: ThreadId, method: string) => {
      const session = sessions.get(threadId);
      return session === undefined
        ? Effect.fail(failRequest(method, "No Open SWE session is active for this thread."))
        : Effect.succeed(session);
    };

    const request = (method: string, req: HttpClientRequest.HttpClientRequest) =>
      httpClient.execute(HttpClientRequest.setHeaders(headers)(req)).pipe(
        Effect.mapError((cause) =>
          failRequest(method, `Could not reach the LangGraph server at ${baseUrl}.`, cause),
        ),
        Effect.filterOrFail(
          (response) => response.status >= 200 && response.status < 300,
          (response) =>
            failRequest(
              method,
              response.status === 401 || response.status === 403
                ? "The LangGraph server rejected the configured API key."
                : `The LangGraph server answered ${String(response.status)}.`,
            ),
        ),
      );

    const jsonRequest = (method: string, req: HttpClientRequest.HttpClientRequest) =>
      request(method, req).pipe(
        Effect.flatMap((response) =>
          response.json.pipe(
            Effect.mapError((cause) =>
              failRequest(method, "The LangGraph server returned a malformed response.", cause),
            ),
          ),
        ),
      );

    const startSession: ProviderAdapterShape<ProviderAdapterRequestError>["startSession"] = (
      input,
    ) =>
      Effect.gen(function* () {
        if (baseUrl.length === 0) {
          return yield* Effect.fail(
            failRequest("startSession", "Set a server URL for Open SWE in Settings."),
          );
        }

        if (
          input.modelSelection !== undefined &&
          input.modelSelection.instanceId !== providerInstanceId
        ) {
          return yield* Effect.fail(
            failRequest(
              "startSession",
              `Open SWE model selection is bound to instance '${String(input.modelSelection.instanceId)}', expected '${String(providerInstanceId)}'.`,
            ),
          );
        }

        if (sessions.has(input.threadId)) {
          yield* stopSession(input.threadId);
        }

        yield* ensureDesktopProjectAllowed(input.cwd);

        const resumed = readResumeCursor(input.resumeCursor);
        const requestedThreadId = resumed?.threadId ?? String(input.threadId);

        const body = yield* jsonRequest(
          "startSession",
          HttpClientRequest.post(`${baseUrl}/threads`).pipe(
            HttpClientRequest.bodyJsonUnsafe({
              thread_id: requestedThreadId,
              if_exists: "do_nothing",
              metadata: { source: "desktop" },
            }),
          ),
        );
        const langGraphThreadId = asRecord(body)?.["thread_id"];
        if (typeof langGraphThreadId !== "string") {
          return yield* Effect.fail(
            failRequest("startSession", "The LangGraph server did not return a thread id."),
          );
        }

        const model = input.modelSelection?.model;
        const effort = getModelSelectionStringOptionValue(input.modelSelection, "effort");
        const createdAt = yield* nowIso;
        sessions.set(input.threadId, {
          langGraphThreadId,
          createdAt,
          cwd: input.cwd,
          runtimeMode: input.runtimeMode,
          model,
          effort,
          planMode: false,
          activeTurnId: undefined,
          activeRunId: undefined,
          activeRunGeneration: 0,
          activeFiber: undefined,
          activeRunConfig: undefined,
          activeEventState: undefined,
          pendingTurns: [],
          pendingApprovals: new Map(),
          approvalDecisionGroups: new Map(),
          pendingUserInputs: new Map(),
          resolvedInterrupts: new Map(),
          lastError: undefined,
        });

        yield* emit({
          type: "session.started",
          threadId: input.threadId,
          payload: { resume: { threadId: langGraphThreadId } },
        });
        yield* emit({
          type: "session.state.changed",
          threadId: input.threadId,
          payload: { state: "ready", reason: "Open SWE session ready" },
        });
        yield* emit({
          type: "thread.started",
          threadId: input.threadId,
          payload: { providerThreadId: langGraphThreadId },
        });

        return {
          provider: DRIVER_KIND,
          providerInstanceId,
          status: "ready",
          runtimeMode: input.runtimeMode,
          threadId: input.threadId,
          createdAt,
          updatedAt: createdAt,
          resumeCursor: { threadId: langGraphThreadId },
          ...(input.cwd === undefined ? {} : { cwd: input.cwd }),
          ...(model === undefined ? {} : { model }),
        } satisfies ProviderSession;
      });

    /**
     * Consume one run's SSE stream. Chunk frames stream incremental text while
     * the trailing `updates` frame repeats the whole assembled message under
     * the same id, so emitted text is tracked per id and only what has not
     * already gone out is emitted.
     */
    const consumeRun = (
      threadId: ThreadId,
      turnId: TurnId,
      session: LangGraphSessionState,
      response: Awaited<ReturnType<typeof request>> extends Effect.Effect<
        infer A,
        infer _E,
        infer _R
      >
        ? A
        : never,
      eventState: LangGraphTurnEventState,
      runGeneration: number,
      reconnectAttempt = 0,
      previousEventId?: string,
    ): Effect.Effect<void> => {
      const {
        emittedText,
        assistantItems,
        startedTools,
        completedTools,
        emittedUsage,
        chunkHistory,
        reasoningItems,
        tasks,
        toolNames,
        namespaceTasks,
        protocolMessages,
      } = eventState;
      const streamChunkCursor = new Map<string, number>();
      let streamedAssistantText = false;
      let lastThreadState: "active" | "idle" | "error" | undefined;
      let lastEventId = previousEventId;
      let streamFailure: string | undefined;
      let currentRaw: RuntimeEventRaw | undefined;
      const emit = (event: Parameters<typeof publishEvent>[0]) =>
        publishEvent(
          event.raw === undefined && currentRaw !== undefined
            ? ({ ...event, raw: currentRaw } as Parameters<typeof publishEvent>[0])
            : event,
        );

      const emitThreadState = (state: "active" | "idle" | "error", detail?: unknown) => {
        if (lastThreadState === state) return Effect.void;
        lastThreadState = state;
        return emit({
          type: "thread.state.changed",
          threadId,
          payload: { state, ...(detail === undefined ? {} : { detail }) },
        });
      };

      const emitReasoningDelta = (messageId: string, index: number, delta: string) =>
        Effect.gen(function* () {
          if (delta.length === 0) return;
          const itemId = `${messageId}:reasoning:${String(index)}`;
          if (!reasoningItems.has(itemId)) {
            addBounded(reasoningItems, itemId);
            yield* emit({
              type: "item.started",
              threadId,
              turnId,
              itemId: RuntimeItemId.make(itemId),
              payload: { itemType: "reasoning", status: "inProgress" },
            });
          }
          const key = `reasoning:${itemId}`;
          setBounded(emittedText, key, (emittedText.get(key) ?? 0) + delta.length);
          yield* emit({
            type: "content.delta",
            threadId,
            turnId,
            itemId: RuntimeItemId.make(itemId),
            payload: { streamKind: "reasoning_text", contentIndex: index, delta },
          });
        });

      const taskPayload = (taskId: string) => {
        const task = tasks.get(taskId);
        return {
          taskId: RuntimeTaskId.make(taskId),
          taskType: task?.taskType ?? "subagent",
          toolUseId: taskId,
          ...(task?.description === undefined
            ? {}
            : { description: task.description, title: task.description }),
        };
      };

      const startTask = (
        taskId: string,
        input: unknown,
        fallbackTaskType = "subagent",
      ): Effect.Effect<void> =>
        Effect.gen(function* () {
          if (tasks.has(taskId)) return;
          const args = asRecord(input);
          const descriptionValue = args?.["description"] ?? args?.["prompt"];
          const description =
            typeof descriptionValue === "string" && descriptionValue.trim().length > 0
              ? descriptionValue.trim()
              : undefined;
          const taskTypeValue = args?.["subagent_type"] ?? args?.["task_type"];
          const taskType =
            typeof taskTypeValue === "string" && taskTypeValue.length > 0
              ? taskTypeValue
              : fallbackTaskType;
          setBounded(tasks, taskId, {
            ...(description === undefined ? {} : { description }),
            taskType,
            completed: false,
          });
          yield* emit({
            type: "task.started",
            threadId,
            turnId,
            payload: taskPayload(taskId),
          });
        });

      const updateTask = (taskId: string, status: "running" | "waiting", detail?: string) =>
        Effect.gen(function* () {
          if (!tasks.has(taskId)) yield* startTask(taskId, undefined);
          yield* emit({
            type: "task.updated",
            threadId,
            turnId,
            payload: {
              ...taskPayload(taskId),
              status,
              ...(detail === undefined ? {} : { description: detail }),
            },
          });
        });

      const completeTask = (
        taskId: string,
        status: "completed" | "failed" | "stopped",
        summary?: string,
      ) =>
        Effect.gen(function* () {
          if (!tasks.has(taskId)) yield* startTask(taskId, undefined);
          const task = tasks.get(taskId);
          if (task?.completed === true) return;
          if (task !== undefined) task.completed = true;
          yield* emit({
            type: "task.completed",
            threadId,
            turnId,
            payload: {
              ...taskPayload(taskId),
              status,
              ...(summary === undefined ? {} : { summary }),
            },
          });
        });

      const startTool = (
        toolCallId: string,
        toolName: string,
        input: unknown,
        namespace: ReadonlyArray<string> = [],
      ) =>
        Effect.gen(function* () {
          const itemType = classifyToolItemType(toolName);
          if (!startedTools.has(toolCallId)) {
            addBounded(startedTools, toolCallId);
            const ownerTaskId = namespaceTasks.get(namespace.join("|"));
            yield* emit({
              type: "item.started",
              threadId,
              turnId,
              itemId: RuntimeItemId.make(toolCallId),
              payload: {
                itemType,
                status: "inProgress",
                title: titleForItemType(itemType, toolName),
                ...(summarize(input) === undefined ? {} : { detail: summarize(input) as string }),
                ...(ownerTaskId === undefined ? {} : { agentId: ownerTaskId }),
                data: { toolCallId, kind: toolName, input },
              },
            });
          }
          if (itemType === "collab_agent_tool_call") yield* startTask(toolCallId, input);
          const plan =
            toolName.toLowerCase() === "write_todos" || toolName.toLowerCase() === "todowrite"
              ? readPlanSteps(input)
              : undefined;
          if (plan !== undefined) {
            const planKey = plan.map((step) => `${step.status}:${step.step}`).join("\n");
            if (eventState.lastPlan !== planKey) {
              eventState.lastPlan = planKey;
              yield* emit({
                type: "turn.plan.updated",
                threadId,
                turnId,
                payload: { explanation: "Open SWE tasks", plan },
              });
            }
          }
        });

      const updateTool = (toolCallId: string, toolName: string, delta: string) =>
        Effect.gen(function* () {
          const itemType = classifyToolItemType(toolName);
          yield* emit({
            type: "item.updated",
            threadId,
            turnId,
            itemId: RuntimeItemId.make(toolCallId),
            payload: {
              itemType,
              status: "inProgress",
              title: titleForItemType(itemType, toolName),
              ...(summarize(delta) === undefined ? {} : { detail: summarize(delta) as string }),
              data: { toolCallId, kind: toolName, outputDelta: delta },
            },
          });
          if (itemType === "command_execution" || itemType === "file_change") {
            yield* emit({
              type: "content.delta",
              threadId,
              turnId,
              itemId: RuntimeItemId.make(toolCallId),
              payload: {
                streamKind:
                  itemType === "command_execution" ? "command_output" : "file_change_output",
                delta,
              },
            });
          }
          if (tasks.has(toolCallId)) {
            yield* emit({
              type: "task.progress",
              threadId,
              turnId,
              payload: {
                ...taskPayload(toolCallId),
                description: tasks.get(toolCallId)?.description ?? "Subagent produced output",
                ...(summarize(delta) === undefined ? {} : { summary: summarize(delta) as string }),
              },
            });
          }
        });

      const completeTool = (
        toolCallId: string,
        toolName: string,
        output: unknown,
        error?: string,
      ) =>
        Effect.gen(function* () {
          if (completedTools.has(toolCallId)) return;
          addBounded(completedTools, toolCallId);
          const itemType = classifyToolItemType(toolName);
          yield* emit({
            type: "item.completed",
            threadId,
            turnId,
            itemId: RuntimeItemId.make(toolCallId),
            payload: {
              itemType,
              status: error === undefined ? "completed" : "failed",
              title: titleForItemType(itemType, toolName),
              ...(summarize(error ?? output) === undefined
                ? {}
                : { detail: summarize(error ?? output) as string }),
              data: { toolCallId, kind: toolName, result: output, ...(error ? { error } : {}) },
            },
          });
          if (tasks.has(toolCallId) || itemType === "collab_agent_tool_call") {
            yield* completeTask(
              toolCallId,
              error === undefined ? "completed" : "failed",
              summarize(error ?? output),
            );
          }
        });

      const emitAssistantDelta = (messageId: string, delta: string, contentIndex?: number) =>
        Effect.gen(function* () {
          if (delta.length === 0) return;
          if (!assistantItems.has(messageId)) {
            addBounded(assistantItems, messageId);
            yield* emit({
              type: "item.started",
              threadId,
              turnId,
              itemId: RuntimeItemId.make(messageId),
              payload: { itemType: "assistant_message", status: "inProgress" },
            });
          }
          setBounded(emittedText, messageId, (emittedText.get(messageId) ?? 0) + delta.length);
          yield* emit({
            type: "content.delta",
            threadId,
            turnId,
            itemId: RuntimeItemId.make(messageId),
            payload: {
              streamKind: "assistant_text",
              ...(contentIndex === undefined ? {} : { contentIndex }),
              delta,
            },
          });
        });

      const handleProtocolMessage = (
        value: unknown,
        namespace: ReadonlyArray<string>,
      ): Effect.Effect<void> =>
        Effect.gen(function* () {
          const data = asRecord(value);
          const event = data?.["event"];
          if (typeof event !== "string") return;
          const namespaceKey = namespace.join("|");
          if (event === "message-start") {
            const id = data?.["id"];
            if (data?.["role"] !== "ai" || typeof id !== "string" || id.length === 0) return;
            setBounded(protocolMessages, namespaceKey, id);
            const metadata = asRecord(data?.["metadata"]);
            if (metadata !== undefined && Object.keys(metadata).length > 0) {
              yield* emit({
                type: "thread.metadata.updated",
                threadId,
                payload: {
                  metadata: {
                    ...(namespace.length === 0 ? {} : { namespace: [...namespace] }),
                    ...metadata,
                  },
                },
              });
            }
            return;
          }
          const messageId = protocolMessages.get(namespaceKey);
          if (event === "error") {
            const message = data?.["message"];
            if (typeof message === "string" && message.length > 0) eventState.failure = message;
            return;
          }
          if (messageId === undefined) return;
          const index = nonNegativeInteger(data?.["index"]) ?? 0;
          if (event === "content-block-start" || event === "content-block-finish") {
            const content = asRecord(data?.["content"]);
            const type = content?.["type"];
            const delta =
              type === "text"
                ? content?.["text"]
                : type === "reasoning"
                  ? content?.["reasoning"]
                  : undefined;
            if (typeof delta === "string" && delta.length > 0) {
              if (type === "reasoning") yield* emitReasoningDelta(messageId, index, delta);
              else yield* emitAssistantDelta(messageId, delta, index);
            }
            if (event === "content-block-finish" && (type === "text" || type === "reasoning")) {
              const itemId =
                type === "reasoning" ? `${messageId}:reasoning:${String(index)}` : messageId;
              yield* emit({
                type: "item.updated",
                threadId,
                turnId,
                itemId: RuntimeItemId.make(itemId),
                payload: {
                  itemType: type === "reasoning" ? "reasoning" : "assistant_message",
                  status: "inProgress",
                  data: { contentIndex: index, content },
                },
              });
            }
            return;
          }
          if (event === "content-block-delta") {
            const delta = asRecord(data?.["delta"]);
            if (delta?.["type"] === "text-delta" && typeof delta["text"] === "string") {
              yield* emitAssistantDelta(messageId, delta["text"], index);
            } else if (
              delta?.["type"] === "reasoning-delta" &&
              typeof delta["reasoning"] === "string"
            ) {
              yield* emitReasoningDelta(messageId, index, delta["reasoning"]);
            }
            return;
          }
          if (event === "message-finish") {
            protocolMessages.delete(namespaceKey);
            const usage = readUsage({ usage_metadata: data?.["usage"] });
            const usageKey = summarize(usage, 1_000);
            if (usage !== undefined && usageKey !== undefined && !emittedUsage.has(usageKey)) {
              addBounded(emittedUsage, usageKey);
              yield* emit({ type: "thread.token-usage.updated", threadId, payload: { usage } });
            }
          }
        });

      const handleToolEvent = (
        value: unknown,
        namespace: ReadonlyArray<string>,
      ): Effect.Effect<void> =>
        Effect.gen(function* () {
          const data = asRecord(value);
          const event = data?.["event"];
          const toolCallId = data?.["tool_call_id"];
          if (
            typeof event !== "string" ||
            typeof toolCallId !== "string" ||
            toolCallId.length === 0
          ) {
            return;
          }
          if (event === "tool-started") {
            const toolName = data?.["tool_name"];
            if (typeof toolName !== "string" || toolName.length === 0) return;
            setBounded(toolNames, toolCallId, toolName);
            yield* startTool(toolCallId, toolName, data?.["input"], namespace);
            return;
          }
          const toolName = toolNames.get(toolCallId) ?? "tool";
          if (event === "tool-output-delta") {
            const delta = data?.["delta"];
            if (typeof delta === "string" && delta.length > 0) {
              yield* updateTool(toolCallId, toolName, delta);
            }
          } else if (event === "tool-finished") {
            yield* completeTool(toolCallId, toolName, data?.["output"]);
          } else if (event === "tool-error") {
            const message = data?.["message"];
            yield* completeTool(
              toolCallId,
              toolName,
              undefined,
              typeof message === "string" && message.length > 0 ? message : "Tool failed",
            );
          }
        });

      const handleLifecycle = (
        value: unknown,
        namespace: ReadonlyArray<string>,
      ): Effect.Effect<void> =>
        Effect.gen(function* () {
          const data = asRecord(value);
          const status = data?.["event"];
          if (typeof status !== "string") return;
          if (namespace.length === 0) {
            if (status === "started" || status === "running")
              yield* emitThreadState("active", data);
            else if (status === "failed") yield* emitThreadState("error", data);
            else if (status === "completed" || status === "interrupted") {
              yield* emitThreadState("idle", data);
            }
            return;
          }
          const cause = asRecord(data?.["cause"]);
          const taskId =
            cause?.["type"] === "toolCall" && typeof cause["tool_call_id"] === "string"
              ? cause["tool_call_id"]
              : namespaceTasks.get(namespace.join("|"));
          if (taskId === undefined || taskId.length === 0) return;
          setBounded(namespaceTasks, namespace.join("|"), taskId);
          const graphName = data?.["graph_name"];
          if (!tasks.has(taskId)) {
            yield* startTask(
              taskId,
              typeof graphName === "string" ? { task_type: graphName, description: graphName } : {},
            );
          }
          if (status === "started" || status === "running") {
            yield* updateTask(taskId, "running");
          } else if (status === "completed") {
            yield* completeTask(taskId, "completed");
          } else if (status === "failed") {
            yield* completeTask(taskId, "failed", summarize(data?.["error"]));
          } else if (status === "interrupted") {
            yield* completeTask(taskId, "stopped");
          }
        });

      const emitPlanAndDiff = (value: unknown): Effect.Effect<void> =>
        Effect.gen(function* () {
          const record = asRecord(value);
          if (record === undefined) return;
          const candidates = [
            record,
            ...Object.values(record).flatMap((item) => {
              const nested = asRecord(item);
              return nested === undefined ? [] : [nested];
            }),
          ];
          for (const candidate of candidates) {
            const plan = readPlanSteps(candidate);
            if (plan !== undefined) {
              const planKey = plan.map((step) => `${step.status}:${step.step}`).join("\n");
              if (eventState.lastPlan !== planKey) {
                eventState.lastPlan = planKey;
                yield* emit({
                  type: "turn.plan.updated",
                  threadId,
                  turnId,
                  payload: { explanation: "Open SWE tasks", plan },
                });
              }
              break;
            }
          }
          const diff = findExplicitDiff(record);
          if (diff !== undefined && diff !== eventState.lastDiff) {
            eventState.lastDiff = diff;
            yield* emit({
              type: "turn.diff.updated",
              threadId,
              turnId,
              payload: { unifiedDiff: diff },
            });
          }
        });

      const handleMessage = (
        raw: unknown,
        fromUpdates = false,
        namespace: ReadonlyArray<string> = [],
      ): Effect.Effect<void> =>
        Effect.gen(function* () {
          const message = asRecord(raw);
          if (message === undefined) return;

          const type = message["type"];
          const id = typeof message["id"] === "string" ? message["id"] : undefined;

          if (type === "tool") {
            const toolCallId = message["tool_call_id"];
            if (typeof toolCallId !== "string" || toolCallId.length === 0) return;
            const toolName =
              typeof message["name"] === "string"
                ? message["name"]
                : (toolNames.get(toolCallId) ?? "tool");
            yield* completeTool(
              toolCallId,
              toolName,
              message["content"],
              message["status"] === "error" ? summarize(message["content"]) : undefined,
            );
            return;
          }

          if (type !== "ai" && type !== "AIMessageChunk") return;

          const usage = readUsage(message);
          const encodedUsage = encodeUnknownJsonStringExit(usage);
          const usageKey = Exit.isSuccess(encodedUsage) ? encodedUsage.value : undefined;
          if (usage !== undefined && usageKey !== undefined && !emittedUsage.has(usageKey)) {
            addBounded(emittedUsage, usageKey);
            yield* emit({
              type: "thread.token-usage.updated",
              threadId,
              payload: { usage },
            });
          }

          const text = extractText(message["content"]);
          const reasoning = reasoningText(message["content"]);
          if (id !== undefined && reasoning.length > 0) {
            const reasoningKey = `legacy-reasoning:${id}`;
            const already = emittedText.get(reasoningKey) ?? 0;
            const delta = type === "AIMessageChunk" ? reasoning : reasoning.slice(already);
            if (delta.length > 0) {
              setBounded(emittedText, reasoningKey, already + delta.length);
              yield* emitReasoningDelta(id, 0, delta);
            }
          }
          if (id !== undefined && text.length > 0 && (!fromUpdates || !streamedAssistantText)) {
            if (!assistantItems.has(id)) {
              addBounded(assistantItems, id);
              yield* emit({
                type: "item.started",
                threadId,
                turnId,
                itemId: RuntimeItemId.make(id),
                payload: { itemType: "assistant_message", status: "inProgress" },
              });
            }
            const already = emittedText.get(id) ?? 0;
            // Chunk frames carry an incremental delta; the trailing `updates`
            // frame carries the whole assembled message under the same id, so
            // only the part not already streamed goes out.
            let delta = type === "AIMessageChunk" ? text : text.slice(already);
            if (type === "AIMessageChunk") {
              const cursor = streamChunkCursor.get(id) ?? 0;
              const history = chunkHistory.get(id) ?? [];
              streamChunkCursor.set(id, cursor + 1);
              if (history[cursor] === text) {
                delta = "";
              } else {
                if (history.length >= MAX_CHUNKS_PER_ITEM) history.shift();
                history.push(text);
                setBounded(chunkHistory, id, history);
              }
            }
            if (delta.length > 0) {
              if (!fromUpdates) streamedAssistantText = true;
              setBounded(emittedText, id, already + delta.length);
              yield* emit({
                type: "content.delta",
                threadId,
                turnId,
                itemId: RuntimeItemId.make(id),
                payload: { streamKind: "assistant_text", delta },
              });
            }
          }

          const toolCalls = message["tool_calls"];
          if (!Array.isArray(toolCalls)) return;
          for (const call of toolCalls) {
            const record = asRecord(call);
            if (record === undefined) continue;
            const toolCallId = record["id"];
            const toolName = typeof record["name"] === "string" ? record["name"] : undefined;
            if (
              typeof toolCallId !== "string" ||
              toolCallId.length === 0 ||
              toolName === undefined
            ) {
              continue;
            }
            setBounded(toolNames, toolCallId, toolName);
            yield* startTool(toolCallId, toolName, record["args"], namespace);
          }
        });

      const handleFrame = (frame: SseEvent): Effect.Effect<void> =>
        Effect.gen(function* () {
          if (frame.id !== undefined) lastEventId = frame.id;
          if (frame.overflow === true) {
            eventState.failure = "The Open SWE run returned an oversized event stream frame.";
            return;
          }
          const decodedFrame = decodeUnknownJsonStringExit(frame.data);
          if (Exit.isFailure(decodedFrame)) {
            eventState.failure = "The Open SWE run returned a malformed event stream frame.";
            return;
          }
          const parsed = decodedFrame.value;
          const eventInfo = parseNamespacedEvent(frame.event);
          if (eventInfo === undefined) return;
          let event = eventInfo.event;
          let namespace = eventInfo.namespace;
          let payload = parsed;
          if (
            Array.isArray(parsed) &&
            parsed.length === 2 &&
            Array.isArray(parsed[0]) &&
            parsed[0].every((part) => typeof part === "string" && part.length > 0)
          ) {
            namespace = parsed[0];
            payload = parsed[1];
          }
          const raw: RuntimeEventRaw = {
            source: "langgraph.sse",
            method: event.slice(0, MAX_SSE_EVENT_ID_LENGTH),
            payload: boundedDiagnosticValue({
              ...(frame.id === undefined ? {} : { id: frame.id }),
              ...(namespace.length === 0 ? {} : { namespace }),
              payload,
            }),
          };
          currentRaw = raw;
          yield* logNativeFrame(threadId, turnId, frame, raw);

          // The run id needed to cancel arrives once, up front, in the
          // metadata frame — not on the data frames.
          if (event === "metadata") {
            const runId = asRecord(payload)?.["run_id"];
            if (typeof runId === "string" && runId.length > 0) {
              session.activeRunId = runId;
              yield* emit({
                type: "thread.metadata.updated",
                threadId,
                payload: { metadata: { runId } },
              });
            }
            return;
          }

          if (event === "error") {
            eventState.failure =
              summarize(asRecord(payload)?.["message"] ?? payload) ?? "The Open SWE run failed.";
            return;
          }

          if (event === "messages" && typeof asRecord(payload)?.["event"] === "string") {
            yield* handleProtocolMessage(payload, namespace);
            return;
          }

          if (event.startsWith("messages")) {
            // messages-tuple frames are [message, metadata]; some servers
            // send the bare message instead.
            const message = Array.isArray(payload) ? payload[0] : payload;
            yield* handleMessage(message, false, namespace);
            return;
          }

          if (event === "tools") {
            yield* handleToolEvent(payload, namespace);
            return;
          }

          if (event === "lifecycle") {
            yield* handleLifecycle(payload, namespace);
            return;
          }

          if (event === "custom" || event.startsWith("custom:")) {
            const custom = asRecord(payload);
            const name = event.startsWith("custom:")
              ? event.slice("custom:".length)
              : typeof custom?.["name"] === "string"
                ? custom["name"]
                : undefined;
            const customPayload = custom?.["payload"];
            const customRecord = asRecord(customPayload);
            if (name === "turn.proposed.delta") {
              const delta = customRecord?.["delta"];
              if (typeof delta === "string" && delta.length > 0) {
                yield* emit({
                  type: "turn.proposed.delta",
                  threadId,
                  turnId,
                  payload: { delta },
                });
              }
            } else if (name === "turn.proposed.completed") {
              const planMarkdown = customRecord?.["planMarkdown"];
              if (typeof planMarkdown === "string" && planMarkdown.trim().length > 0) {
                yield* emit({
                  type: "turn.proposed.completed",
                  threadId,
                  turnId,
                  payload: { planMarkdown: planMarkdown.trim() },
                });
              }
            } else {
              yield* emitPlanAndDiff(customPayload);
            }
            return;
          }

          if (event === "updates" || event === "values") {
            const record = asRecord(payload);
            if (record === undefined) return;
            yield* emitPlanAndDiff(record);
            if ("__interrupt__" in record) {
              const interrupts = readInterrupts(record["__interrupt__"]);
              if (interrupts === undefined) {
                eventState.failure = "The Open SWE run returned a malformed interrupt request.";
                return;
              }
              for (const parsedInterrupt of interrupts) {
                const interruptId = parsedInterrupt.interrupt.id;
                if (
                  session.approvalDecisionGroups.has(interruptId) ||
                  session.pendingUserInputs.has(interruptId) ||
                  session.resolvedInterrupts.has(interruptId)
                ) {
                  continue;
                }
                if (parsedInterrupt.kind === "userInput") {
                  const userInput = parsedInterrupt.interrupt;
                  if (session.pendingUserInputs.size >= MAX_PENDING_INTERRUPT_ENTRIES) {
                    eventState.failure = "The Open SWE run returned too many interrupt requests.";
                    return;
                  }
                  session.pendingUserInputs.set(interruptId, userInput);
                  yield* emit({
                    type: "user-input.requested",
                    threadId,
                    turnId,
                    requestId: RuntimeRequestId.make(interruptId),
                    payload: { questions: userInput.questions },
                  });
                  continue;
                }
                const approval = parsedInterrupt.interrupt;
                if (
                  session.approvalDecisionGroups.size >= MAX_PENDING_INTERRUPT_ENTRIES ||
                  session.pendingApprovals.size + approval.actions.length >
                    MAX_PENDING_INTERRUPT_ENTRIES
                ) {
                  eventState.failure = "The Open SWE run returned too many interrupt requests.";
                  return;
                }
                const optionSets = approval.actions.map((action) => [
                  ...(action.allowedDecisions.includes("approve")
                    ? [{ decision: "accept" as const, label: "Allow once" }]
                    : []),
                  ...(action.allowedDecisions.includes("reject")
                    ? [{ decision: "decline" as const, label: "Decline" }]
                    : []),
                ]);
                if (optionSets.some((options) => options.length === 0)) {
                  eventState.failure =
                    "The Open SWE run requested an approval decision this client cannot represent.";
                  return;
                }
                session.approvalDecisionGroups.set(
                  approval.id,
                  approval.actions.map(() => undefined),
                );
                for (const [actionIndex, action] of approval.actions.entries()) {
                  const requestKey =
                    approval.actions.length === 1
                      ? approval.id
                      : `${approval.id}:${String(actionIndex)}`;
                  const requestId = ApprovalRequestId.make(requestKey);
                  const requestType = classifyRequestType([action]);
                  const detail = summarize(
                    action.description ?? `${action.name}: ${summarize(action.args)}`,
                  );
                  session.pendingApprovals.set(requestKey, {
                    interruptId: approval.id,
                    actionIndex,
                    actionCount: approval.actions.length,
                    action,
                    requestId,
                    requestType,
                  });
                  yield* emit({
                    type: "request.opened",
                    threadId,
                    turnId,
                    requestId: RuntimeRequestId.make(requestKey),
                    payload: {
                      requestType,
                      ...(detail === undefined ? {} : { detail }),
                      options: optionSets[actionIndex],
                      args: { action, actionIndex, actionCount: approval.actions.length },
                    },
                  });
                }
              }
              return;
            }
            for (const value of Object.values(record)) {
              const messages = asRecord(value)?.["messages"];
              if (!Array.isArray(messages)) continue;
              for (const message of messages) {
                yield* handleMessage(message, true, namespace);
              }
            }
          }
        }).pipe(
          Effect.ensuring(
            Effect.sync(() => {
              currentRaw = undefined;
            }),
          ),
        );

      return decodeSse(response.stream).pipe(
        Stream.runForEach(handleFrame),
        Effect.catchCause((cause) =>
          Effect.sync(() => {
            streamFailure = Cause.pretty(cause);
          }),
        ),
        Effect.andThen(() =>
          Effect.gen(function* () {
            if (session.activeTurnId !== turnId || session.activeRunGeneration !== runGeneration) {
              return;
            }
            if (
              eventState.failure === undefined &&
              (session.pendingApprovals.size > 0 || session.pendingUserInputs.size > 0)
            ) {
              session.activeFiber = undefined;
              session.activeRunId = undefined;
              yield* emit({
                type: "session.state.changed",
                threadId,
                payload: { state: "waiting", reason: "Open SWE is waiting for user input" },
              });
              return;
            }
            if (eventState.failure === undefined && session.resolvedInterrupts.size > 0) {
              session.activeFiber = undefined;
              session.activeRunId = undefined;
              yield* resumeTurn(session, threadId, turnId, eventState);
              return;
            }
            if (eventState.failure === undefined) {
              const runId = session.activeRunId;
              if (runId === undefined) {
                if (streamFailure !== undefined) {
                  eventState.failure = "The Open SWE run stream disconnected before completion.";
                }
              } else {
                const encodedThreadId = encodeURIComponent(session.langGraphThreadId);
                const encodedRunId = encodeURIComponent(runId);
                const runResult = yield* jsonRequest(
                  "reconcileTurn",
                  HttpClientRequest.get(
                    `${baseUrl}/threads/${encodedThreadId}/runs/${encodedRunId}`,
                  ),
                ).pipe(Effect.result);
                if (runResult._tag === "Failure") {
                  eventState.failure =
                    "The Open SWE run stream closed before its status could be verified.";
                } else {
                  const run = asRecord(runResult.success);
                  const status = run?.["status"];
                  if (status === "pending" || status === "running") {
                    if (reconnectAttempt >= MAX_RUN_STREAM_RECONNECTS) {
                      eventState.failure =
                        "The Open SWE run stream repeatedly disconnected while the run remained active.";
                    } else {
                      const reconnectResult = yield* request(
                        "reconnectTurn",
                        HttpClientRequest.get(
                          `${baseUrl}/threads/${encodedThreadId}/runs/${encodedRunId}/stream`,
                        ).pipe(HttpClientRequest.setHeader("last-event-id", lastEventId ?? "-1")),
                      ).pipe(Effect.result);
                      if (reconnectResult._tag === "Failure") {
                        eventState.failure = "The Open SWE run stream could not be reconnected.";
                      } else {
                        return yield* consumeRun(
                          threadId,
                          turnId,
                          session,
                          reconnectResult.success,
                          eventState,
                          runGeneration,
                          reconnectAttempt + 1,
                          lastEventId,
                        );
                      }
                    }
                  } else if (status === "success" || status === "completed") {
                    // The durable run state is authoritative even if the SSE transport closed.
                  } else if (
                    status === "error" ||
                    status === "timeout" ||
                    status === "interrupted"
                  ) {
                    const detail = summarize(run?.["error"]);
                    eventState.failure =
                      detail === undefined
                        ? `The Open SWE run ended with status '${status}'.`
                        : `The Open SWE run ended with status '${status}': ${detail}`;
                  } else {
                    eventState.failure = "The Open SWE server returned malformed run status.";
                  }
                }
              }
            }
            session.activeFiber = undefined;
            session.activeRunId = undefined;
            if (eventState.failure !== undefined) {
              for (const pending of session.pendingApprovals.values()) {
                yield* emit({
                  type: "request.resolved",
                  threadId,
                  turnId,
                  requestId: RuntimeRequestId.make(String(pending.requestId)),
                  payload: { requestType: pending.requestType, decision: "cancel" },
                });
              }
              session.pendingApprovals.clear();
              session.approvalDecisionGroups.clear();
              for (const pending of session.pendingUserInputs.values()) {
                yield* emit({
                  type: "user-input.resolved",
                  threadId,
                  turnId,
                  requestId: RuntimeRequestId.make(String(pending.requestId)),
                  payload: { answers: {} },
                });
              }
              session.pendingUserInputs.clear();
              session.resolvedInterrupts.clear();
            }
            for (const itemId of assistantItems) {
              yield* emit({
                type: "item.completed",
                threadId,
                turnId,
                itemId: RuntimeItemId.make(itemId),
                payload: {
                  itemType: "assistant_message",
                  status: eventState.failure === undefined ? "completed" : "failed",
                },
              });
            }
            for (const itemId of reasoningItems) {
              yield* emit({
                type: "item.completed",
                threadId,
                turnId,
                itemId: RuntimeItemId.make(itemId),
                payload: {
                  itemType: "reasoning",
                  status: eventState.failure === undefined ? "completed" : "failed",
                },
              });
            }
            for (const [taskId, task] of tasks) {
              if (!task.completed) {
                yield* completeTask(
                  taskId,
                  eventState.failure === undefined ? "completed" : "failed",
                  eventState.failure,
                );
              }
            }
            session.lastError = eventState.failure;
            session.activeTurnId = undefined;
            session.activeRunConfig = undefined;
            session.activeEventState = undefined;
            yield* emit({
              type: "turn.completed",
              threadId,
              turnId,
              payload:
                eventState.failure === undefined
                  ? { state: "completed" }
                  : { state: "failed", errorMessage: eventState.failure },
            });
            if (eventState.failure !== undefined) {
              yield* emit({
                type: "runtime.error",
                threadId,
                turnId,
                payload: { message: eventState.failure },
              });
            }
            yield* emit({
              type: "session.state.changed",
              threadId,
              payload:
                eventState.failure === undefined
                  ? { state: "ready", reason: "Open SWE turn completed" }
                  : { state: "error", reason: eventState.failure },
            });
            yield* startNextQueued(session);
          }),
        ),
      );
    };

    const resumeTurn = (
      session: LangGraphSessionState,
      threadId: ThreadId,
      turnId: TurnId,
      eventState: LangGraphTurnEventState,
    ): Effect.Effect<void> => {
      const resume = Object.fromEntries(session.resolvedInterrupts);
      session.resolvedInterrupts.clear();
      return Effect.gen(function* () {
        const response = yield* request(
          "respondToRequest",
          HttpClientRequest.post(
            `${baseUrl}/threads/${encodeURIComponent(session.langGraphThreadId)}/runs/stream`,
          ).pipe(
            HttpClientRequest.bodyJsonUnsafe({
              assistant_id: config.graphId,
              command: { resume },
              stream_resumable: true,
              stream_mode: LANGGRAPH_STREAM_MODES,
              stream_subgraphs: true,
              ...(session.activeRunConfig === undefined ? {} : { config: session.activeRunConfig }),
            }),
          ),
        );
        yield* emit({
          type: "session.state.changed",
          threadId,
          payload: { state: "running", reason: "Open SWE permission resolved" },
        });
        const runGeneration = ++session.activeRunGeneration;
        const fiber = yield* consumeRun(
          threadId,
          turnId,
          session,
          response,
          eventState,
          runGeneration,
        ).pipe(Effect.forkIn(providerScope));
        if (session.activeTurnId === turnId && session.activeRunGeneration === runGeneration) {
          session.activeFiber = fiber;
        }
      }).pipe(
        Effect.catch((cause) =>
          Effect.gen(function* () {
            const message = cause.message;
            eventState.failure = message;
            session.lastError = message;
            session.activeTurnId = undefined;
            session.activeRunId = undefined;
            session.activeFiber = undefined;
            session.activeRunConfig = undefined;
            session.activeEventState = undefined;
            yield* emit({
              type: "turn.completed",
              threadId,
              turnId,
              payload: { state: "failed", errorMessage: message },
            });
            yield* emit({
              type: "runtime.error",
              threadId,
              turnId,
              payload: { message },
            });
            yield* emit({
              type: "session.state.changed",
              threadId,
              payload: { state: "error", reason: message },
            });
            yield* startNextQueued(session);
          }),
        ),
      );
    };

    const startTurn = (session: LangGraphSessionState, pending: LangGraphPendingTurn) =>
      Effect.gen(function* () {
        const response = yield* request(
          "sendTurn",
          HttpClientRequest.post(
            `${baseUrl}/threads/${encodeURIComponent(session.langGraphThreadId)}/runs/stream`,
          ).pipe(HttpClientRequest.bodyJsonUnsafe(pending.body)),
        );

        yield* emit({
          type: "turn.started",
          threadId: pending.threadId,
          turnId: pending.turnId,
          payload: {
            ...(pending.model === undefined ? {} : { model: pending.model }),
            ...(pending.effort === undefined ? {} : { effort: pending.effort }),
          },
        });
        yield* emit({
          type: "session.state.changed",
          threadId: pending.threadId,
          payload: { state: "running", reason: "Open SWE turn running" },
        });

        session.activeTurnId = pending.turnId;
        session.activeRunConfig = asRecord(pending.body["config"]);
        const eventState: LangGraphTurnEventState = {
          emittedText: new Map(),
          assistantItems: new Set(),
          startedTools: new Set(),
          completedTools: new Set(),
          emittedUsage: new Set(),
          chunkHistory: new Map(),
          reasoningItems: new Set(),
          tasks: new Map(),
          toolNames: new Map(),
          namespaceTasks: new Map(),
          protocolMessages: new Map(),
          lastPlan: undefined,
          lastDiff: undefined,
          failure: undefined,
        };
        session.activeEventState = eventState;
        session.lastError = undefined;
        const runGeneration = ++session.activeRunGeneration;
        const fiber = yield* consumeRun(
          pending.threadId,
          pending.turnId,
          session,
          response,
          eventState,
          runGeneration,
        ).pipe(Effect.forkIn(providerScope));
        if (
          session.activeTurnId === pending.turnId &&
          session.activeRunGeneration === runGeneration
        ) {
          session.activeFiber = fiber;
        }
      });

    const steerTurn = (
      session: LangGraphSessionState,
      pending: LangGraphPendingTurn,
      eventState: LangGraphTurnEventState,
    ) =>
      Effect.gen(function* () {
        const previousRunGeneration = session.activeRunGeneration;
        const runGeneration = previousRunGeneration + 1;
        session.activeRunGeneration = runGeneration;
        const response = yield* request(
          "sendTurn",
          HttpClientRequest.post(
            `${baseUrl}/threads/${encodeURIComponent(session.langGraphThreadId)}/runs/stream`,
          ).pipe(
            HttpClientRequest.bodyJsonUnsafe({
              ...pending.body,
              multitask_strategy: "interrupt",
            }),
          ),
        ).pipe(
          Effect.tapError(() =>
            Effect.sync(() => {
              if (session.activeRunGeneration === runGeneration) {
                session.activeRunGeneration = previousRunGeneration;
              }
            }),
          ),
        );

        const previousFiber = session.activeFiber;
        session.activeFiber = undefined;
        session.activeRunId = undefined;
        if (previousFiber !== undefined) yield* Fiber.interrupt(previousFiber);
        for (const approval of session.pendingApprovals.values()) {
          yield* emit({
            type: "request.resolved",
            threadId: pending.threadId,
            turnId: pending.turnId,
            requestId: RuntimeRequestId.make(String(approval.requestId)),
            payload: { requestType: approval.requestType, decision: "cancel" },
          });
        }
        session.pendingApprovals.clear();
        session.approvalDecisionGroups.clear();
        for (const userInput of session.pendingUserInputs.values()) {
          yield* emit({
            type: "user-input.resolved",
            threadId: pending.threadId,
            turnId: pending.turnId,
            requestId: RuntimeRequestId.make(String(userInput.requestId)),
            payload: { answers: {} },
          });
        }
        session.pendingUserInputs.clear();
        session.resolvedInterrupts.clear();
        session.activeRunConfig = asRecord(pending.body["config"]);
        eventState.failure = undefined;
        session.lastError = undefined;
        yield* emit({
          type: "session.state.changed",
          threadId: pending.threadId,
          payload: { state: "running", reason: "Open SWE turn steered" },
        });
        const fiber = yield* consumeRun(
          pending.threadId,
          pending.turnId,
          session,
          response,
          eventState,
          runGeneration,
        ).pipe(Effect.forkIn(providerScope));
        if (
          session.activeTurnId === pending.turnId &&
          session.activeRunGeneration === runGeneration
        ) {
          session.activeFiber = fiber;
        }
      });

    const startNextQueued = (session: LangGraphSessionState): Effect.Effect<void> => {
      const pending = session.pendingTurns.shift();
      if (pending === undefined || !sessions.has(pending.threadId)) return Effect.void;
      return startTurn(session, pending).pipe(
        Effect.catch((cause) =>
          Effect.gen(function* () {
            const message = cause.message;
            session.lastError = message;
            yield* emit({
              type: "turn.completed",
              threadId: pending.threadId,
              turnId: pending.turnId,
              payload: { state: "failed", errorMessage: message },
            });
            yield* emit({
              type: "runtime.error",
              threadId: pending.threadId,
              turnId: pending.turnId,
              payload: { message },
            });
            yield* startNextQueued(session);
          }),
        ),
      );
    };

    const sendTurn: ProviderAdapterShape<ProviderAdapterError>["sendTurn"] = (input) =>
      Effect.gen(function* () {
        const session = yield* requireSession(input.threadId, "sendTurn");
        if (
          input.modelSelection !== undefined &&
          input.modelSelection.instanceId !== providerInstanceId
        ) {
          return yield* new ProviderAdapterValidationError({
            provider: DRIVER_KIND,
            operation: "sendTurn",
            issue: `Open SWE model selection is bound to instance '${String(input.modelSelection.instanceId)}', expected '${String(providerInstanceId)}'.`,
          });
        }
        const model = input.modelSelection?.model ?? session.model;
        const effort =
          getModelSelectionStringOptionValue(input.modelSelection, "effort") ?? session.effort;
        session.model = model;
        session.effort = effort;
        if (input.interactionMode === "plan") session.planMode = true;
        if (input.interactionMode === "default") session.planMode = false;

        const content = yield* Effect.forEach(input.attachments ?? [], (attachment) =>
          resolveImageContent(attachment),
        ).pipe(
          Effect.map((images) =>
            images.length === 0
              ? (input.input ?? "")
              : [
                  ...(input.input === undefined ? [] : [{ type: "text", text: input.input }]),
                  ...images,
                ],
          ),
        );

        const currentTimeMillis = yield* Clock.currentTimeMillis;
        const turnId =
          session.activeTurnId ??
          TurnId.make(`langgraph-turn-${String(currentTimeMillis)}-${String(++turnSeq)}`);
        const runBody: Record<string, unknown> = {
          assistant_id: config.graphId,
          input: { messages: [{ role: "user", content }] },
          stream_resumable: true,
          stream_mode: LANGGRAPH_STREAM_MODES,
          stream_subgraphs: true,
          config: {
            configurable: {
              source: "desktop",
              ...(session.cwd === undefined ? {} : { local_project_path: session.cwd }),
              ...(model === undefined ? {} : { agent_model_id: model }),
              ...(effort === undefined ? {} : { agent_effort: effort }),
              plan_mode: session.planMode,
              runtime_mode: session.runtimeMode,
              __event_streaming_v2: true,
            },
          },
        };

        const pending = { threadId: input.threadId, turnId, body: runBody, model, effort };
        if (session.activeTurnId === undefined) {
          yield* startTurn(session, pending);
        } else {
          const eventState = session.activeEventState;
          if (eventState === undefined) {
            return yield* Effect.fail(
              failRequest("sendTurn", "The active Open SWE turn has no event stream state."),
            );
          }
          yield* steerTurn(session, pending, eventState);
        }

        return {
          threadId: input.threadId,
          turnId,
          resumeCursor: { threadId: session.langGraphThreadId },
        };
      });

    const interruptTurn: ProviderAdapterShape<ProviderAdapterRequestError>["interruptTurn"] = (
      threadId,
      requestedTurnId,
    ) =>
      Effect.gen(function* () {
        const session = yield* requireSession(threadId, "interruptTurn");
        const turnId = session.activeTurnId;
        if (requestedTurnId !== undefined && requestedTurnId !== turnId) {
          const index = session.pendingTurns.findIndex(
            (pending) => pending.turnId === requestedTurnId,
          );
          if (index < 0) return;
          session.pendingTurns.splice(index, 1);
          yield* emit({
            type: "turn.aborted",
            threadId,
            turnId: requestedTurnId,
            payload: { reason: "Queued Open SWE turn cancelled from Open SWE." },
          });
          return;
        }
        if (turnId === undefined) return;

        if (session.activeRunId !== undefined) {
          yield* request(
            "interruptTurn",
            HttpClientRequest.post(
              `${baseUrl}/threads/${encodeURIComponent(session.langGraphThreadId)}/runs/${encodeURIComponent(session.activeRunId)}/cancel`,
            ),
          ).pipe(Effect.ignore);
        }
        session.activeTurnId = undefined;
        session.activeRunId = undefined;
        if (session.activeFiber !== undefined) {
          yield* Fiber.interrupt(session.activeFiber);
        }
        session.activeFiber = undefined;
        session.activeRunConfig = undefined;
        session.activeEventState = undefined;
        for (const pending of session.pendingApprovals.values()) {
          yield* emit({
            type: "request.resolved",
            threadId,
            turnId,
            requestId: RuntimeRequestId.make(String(pending.requestId)),
            payload: { requestType: pending.requestType, decision: "cancel" },
          });
        }
        session.pendingApprovals.clear();
        session.approvalDecisionGroups.clear();
        for (const pending of session.pendingUserInputs.values()) {
          yield* emit({
            type: "user-input.resolved",
            threadId,
            turnId,
            requestId: RuntimeRequestId.make(String(pending.requestId)),
            payload: { answers: {} },
          });
        }
        session.pendingUserInputs.clear();
        session.resolvedInterrupts.clear();

        yield* emit({
          type: "turn.aborted",
          threadId,
          turnId,
          payload: { reason: "Interrupted from Open SWE." },
        });
        yield* emit({
          type: "session.state.changed",
          threadId,
          payload: { state: "ready", reason: "Open SWE turn interrupted" },
        });
        yield* startNextQueued(session);
      });

    const respondToRequest: ProviderAdapterShape<ProviderAdapterRequestError>["respondToRequest"] =
      (threadId, requestId, decision) =>
        Effect.gen(function* () {
          const session = yield* requireSession(threadId, "respondToRequest");
          const key = String(requestId);
          const pending = session.pendingApprovals.get(key);
          if (pending === undefined) {
            return yield* Effect.fail(
              failRequest("respondToRequest", `Unknown Open SWE approval request '${key}'.`),
            );
          }
          const turnId = session.activeTurnId;
          const eventState = session.activeEventState;
          if (turnId === undefined || eventState === undefined) {
            return yield* Effect.fail(
              failRequest("respondToRequest", "The Open SWE approval turn is no longer active."),
            );
          }

          const mappedDecision = hitlDecision(pending.action, decision);
          if (mappedDecision === undefined) {
            return yield* Effect.fail(
              failRequest(
                "respondToRequest",
                `Decision '${decision}' is not allowed for Open SWE approval request '${key}'.`,
              ),
            );
          }
          const groupedDecisions = session.approvalDecisionGroups.get(pending.interruptId);
          if (groupedDecisions === undefined || groupedDecisions.length !== pending.actionCount) {
            return yield* Effect.fail(
              failRequest("respondToRequest", "The Open SWE approval group is no longer active."),
            );
          }

          session.pendingApprovals.delete(key);
          groupedDecisions[pending.actionIndex] = mappedDecision;
          if (groupedDecisions.every((entry) => entry !== undefined)) {
            session.approvalDecisionGroups.delete(pending.interruptId);
            session.resolvedInterrupts.set(pending.interruptId, {
              decisions: groupedDecisions,
            } satisfies LangGraphHitlResponse);
          }
          yield* emit({
            type: "request.resolved",
            threadId,
            turnId,
            requestId: RuntimeRequestId.make(key),
            payload: { requestType: pending.requestType, decision },
          });

          if (session.pendingApprovals.size === 0 && session.pendingUserInputs.size === 0) {
            yield* resumeTurn(session, threadId, turnId, eventState);
          }
        });

    const respondToUserInput: ProviderAdapterShape<ProviderAdapterRequestError>["respondToUserInput"] =
      (threadId, requestId, answers) =>
        Effect.gen(function* () {
          const session = yield* requireSession(threadId, "respondToUserInput");
          const key = String(requestId);
          const pending = session.pendingUserInputs.get(key);
          if (pending === undefined) {
            return yield* Effect.fail(
              failRequest("respondToUserInput", `Unknown Open SWE user-input request '${key}'.`),
            );
          }
          const turnId = session.activeTurnId;
          const eventState = session.activeEventState;
          const answerRecord = asRecord(answers);
          if (turnId === undefined || eventState === undefined) {
            return yield* Effect.fail(
              failRequest(
                "respondToUserInput",
                "The Open SWE user-input turn is no longer active.",
              ),
            );
          }
          if (answerRecord === undefined) {
            return yield* Effect.fail(
              failRequest("respondToUserInput", "Open SWE user-input answers must be an object."),
            );
          }
          if (Object.keys(answerRecord).length > MAX_USER_INPUT_QUESTIONS) {
            return yield* Effect.fail(
              failRequest("respondToUserInput", "Open SWE user-input answers are too large."),
            );
          }

          const safeAnswers = (asRecord(boundedDiagnosticValue(answerRecord)) ??
            {}) as ProviderUserInputAnswers;
          session.pendingUserInputs.delete(key);
          session.resolvedInterrupts.set(pending.id, safeAnswers);
          yield* emit({
            type: "user-input.resolved",
            threadId,
            turnId,
            requestId: RuntimeRequestId.make(key),
            payload: { answers: safeAnswers },
          });
          if (session.pendingApprovals.size === 0 && session.pendingUserInputs.size === 0) {
            yield* resumeTurn(session, threadId, turnId, eventState);
          }
        });

    const stopSession: ProviderAdapterShape<ProviderAdapterRequestError>["stopSession"] = (
      threadId,
    ) =>
      Effect.gen(function* () {
        const session = sessions.get(threadId);
        if (session === undefined) return;
        const activeTurnId = session.activeTurnId;
        session.activeTurnId = undefined;
        session.activeRunId = undefined;
        if (session.activeFiber !== undefined) {
          yield* Fiber.interrupt(session.activeFiber);
        }
        for (const pending of session.pendingApprovals.values()) {
          yield* emit({
            type: "request.resolved",
            threadId,
            ...(activeTurnId === undefined ? {} : { turnId: activeTurnId }),
            requestId: RuntimeRequestId.make(String(pending.requestId)),
            payload: { requestType: pending.requestType, decision: "cancel" },
          });
        }
        session.pendingApprovals.clear();
        session.approvalDecisionGroups.clear();
        for (const pending of session.pendingUserInputs.values()) {
          yield* emit({
            type: "user-input.resolved",
            threadId,
            ...(activeTurnId === undefined ? {} : { turnId: activeTurnId }),
            requestId: RuntimeRequestId.make(String(pending.requestId)),
            payload: { answers: {} },
          });
        }
        session.pendingUserInputs.clear();
        session.resolvedInterrupts.clear();
        for (const pending of session.pendingTurns.splice(0)) {
          yield* emit({
            type: "turn.aborted",
            threadId,
            turnId: pending.turnId,
            payload: { reason: "Open SWE session stopped before this queued turn ran." },
          });
        }
        sessions.delete(threadId);
        yield* emit({
          type: "session.exited",
          threadId,
          payload: { exitKind: "graceful" },
        });
      });

    const resolveImageContent = (attachment: ChatAttachment) =>
      Effect.gen(function* () {
        if (!isProviderSendTurnSupportedImageMimeType(attachment.mimeType)) {
          return yield* Effect.fail(
            failRequest("sendTurn", `Open SWE does not support ${attachment.mimeType} images.`),
          );
        }
        if (dependencies.attachmentsDir === undefined) {
          return yield* Effect.fail(
            failRequest("sendTurn", "The Open SWE attachment store is not configured."),
          );
        }
        const attachmentPath = resolveAttachmentPath({
          attachmentsDir: dependencies.attachmentsDir,
          attachment,
        });
        if (attachmentPath === null) {
          return yield* Effect.fail(
            failRequest("sendTurn", `Attachment '${attachment.name}' has an invalid storage id.`),
          );
        }
        const bytes = yield* fileSystem
          .readFile(attachmentPath)
          .pipe(
            Effect.mapError((cause) =>
              failRequest("sendTurn", `Attachment '${attachment.name}' could not be read.`, cause),
            ),
          );
        if (bytes.byteLength !== attachment.sizeBytes) {
          return yield* Effect.fail(
            failRequest("sendTurn", `Attachment '${attachment.name}' has an unexpected size.`),
          );
        }
        return {
          type: "image_url" as const,
          image_url: {
            url: `data:${attachment.mimeType};base64,${Buffer.from(bytes).toString("base64")}`,
          },
        };
      });

    const readThread: ProviderAdapterShape<ProviderAdapterRequestError>["readThread"] = (
      threadId,
    ) =>
      Effect.gen(function* () {
        const session = yield* requireSession(threadId, "readThread");
        const state = yield* jsonRequest(
          "readThread",
          HttpClientRequest.get(
            `${baseUrl}/threads/${encodeURIComponent(session.langGraphThreadId)}/state`,
          ),
        );
        const messages = snapshotMessages(state);
        if (messages === undefined) {
          return yield* Effect.fail(
            failRequest("readThread", "The LangGraph server returned malformed thread state."),
          );
        }
        return snapshotTurns(threadId, messages);
      });

    const rollbackThread: ProviderAdapterShape<ProviderAdapterError>["rollbackThread"] = (
      threadId,
      numTurns,
    ) =>
      Effect.gen(function* () {
        if (!Number.isInteger(numTurns) || numTurns < 1) {
          return yield* new ProviderAdapterValidationError({
            provider: DRIVER_KIND,
            operation: "rollbackThread",
            issue: "numTurns must be an integer >= 1.",
          });
        }

        const session = yield* requireSession(threadId, "rollbackThread");
        if (
          session.activeTurnId !== undefined ||
          session.activeFiber !== undefined ||
          session.pendingTurns.length > 0 ||
          session.pendingApprovals.size > 0
        ) {
          return yield* Effect.fail(
            failRequest("rollbackThread", "Wait for the active Open SWE turn to finish first."),
          );
        }

        const encodedThreadId = encodeURIComponent(session.langGraphThreadId);
        const seenCursors = new Set<string>();
        let before: string | undefined;
        let retainedTurns: number | undefined;
        let target: LangGraphCheckpoint | undefined;
        let zeroTurnParent: LangGraphCheckpoint | undefined;
        const pageSize = 100;

        for (let pageNumber = 0; pageNumber < 100 && target === undefined; pageNumber += 1) {
          const response = yield* jsonRequest(
            "rollbackThread",
            HttpClientRequest.post(`${baseUrl}/threads/${encodedThreadId}/history`).pipe(
              HttpClientRequest.bodyJsonUnsafe({
                limit: pageSize,
                ...(before === undefined ? {} : { before }),
              }),
            ),
          );
          if (!Array.isArray(response)) {
            return yield* Effect.fail(
              failRequest("rollbackThread", "The LangGraph server returned malformed history."),
            );
          }
          if (response.length === 0) break;

          let oldestCheckpointId: string | undefined;
          for (const rawSnapshot of response) {
            const snapshot = asRecord(rawSnapshot);
            const checkpoint = readCheckpoint(snapshot?.["checkpoint"], session.langGraphThreadId);
            const messages = snapshotMessages(snapshot);
            if (snapshot === undefined || checkpoint === undefined || messages === undefined) {
              return yield* Effect.fail(
                failRequest("rollbackThread", "The LangGraph server returned malformed history."),
              );
            }
            oldestCheckpointId = checkpoint.checkpoint_id;
            const turnsAtCheckpoint = userTurnCount(messages);
            retainedTurns ??= Math.max(0, turnsAtCheckpoint - numTurns);
            if (retainedTurns === 0 && turnsAtCheckpoint === 1) {
              zeroTurnParent = readCheckpoint(
                snapshot["parent_checkpoint"],
                session.langGraphThreadId,
              );
            }
            if (turnsAtCheckpoint === retainedTurns) {
              target = checkpoint;
              break;
            }
          }

          if (target !== undefined || response.length < pageSize) break;
          if (oldestCheckpointId === undefined || seenCursors.has(oldestCheckpointId)) {
            return yield* Effect.fail(
              failRequest("rollbackThread", "LangGraph history pagination did not advance."),
            );
          }
          seenCursors.add(oldestCheckpointId);
          before = oldestCheckpointId;
        }

        if (target === undefined && retainedTurns === 0) target = zeroTurnParent;

        if (target === undefined) {
          return yield* Effect.fail(
            failRequest(
              "rollbackThread",
              "LangGraph history has no checkpoint at the requested turn boundary.",
            ),
          );
        }
        if (session.activeTurnId !== undefined || session.activeFiber !== undefined) {
          return yield* Effect.fail(
            failRequest("rollbackThread", "An Open SWE turn started while rollback was loading."),
          );
        }

        yield* jsonRequest(
          "rollbackThread",
          HttpClientRequest.post(`${baseUrl}/threads/${encodedThreadId}/state`).pipe(
            HttpClientRequest.bodyJsonUnsafe({ values: null, checkpoint: target }),
          ),
        );
        return yield* readThread(threadId);
      });

    return {
      provider: DRIVER_KIND,
      capabilities: { sessionModelSwitch: "in-session" },
      startSession,
      sendTurn,
      interruptTurn,
      respondToRequest,
      respondToUserInput,
      stopSession,
      listSessions: () =>
        Effect.map(
          nowIso,
          (createdAt) =>
            [...sessions.entries()].map(([threadId, session]) => ({
              provider: DRIVER_KIND,
              providerInstanceId,
              status:
                session.lastError !== undefined
                  ? "error"
                  : session.activeTurnId === undefined
                    ? "ready"
                    : "running",
              runtimeMode: session.runtimeMode,
              threadId,
              createdAt: session.createdAt,
              updatedAt: createdAt,
              resumeCursor: { threadId: session.langGraphThreadId },
              ...(session.cwd === undefined ? {} : { cwd: session.cwd }),
              ...(session.model === undefined ? {} : { model: session.model }),
              ...(session.activeTurnId === undefined ? {} : { activeTurnId: session.activeTurnId }),
              ...(session.lastError === undefined ? {} : { lastError: session.lastError }),
            })) satisfies ReadonlyArray<ProviderSession>,
        ),
      hasSession: (threadId) => Effect.succeed(sessions.has(threadId)),
      // LangGraph state is authoritative when a session is resumed in a fresh app process.
      readThread,
      rollbackThread,
      stopAll: () =>
        Effect.forEach([...sessions.keys()], stopSession, { discard: true }).pipe(Effect.asVoid),
      streamEvents: Stream.fromPubSub(runtimeEvents),
    } satisfies ProviderAdapterShape<ProviderAdapterError>;
  });
}
