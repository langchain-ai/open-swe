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
  type ChatAttachment,
  type CanonicalItemType,
  EventId,
  type LangGraphSettings,
  ProviderDriverKind,
  type ProviderInstanceId,
  type ProviderRuntimeEvent,
  type ProviderSession,
  isProviderSendTurnSupportedImageMimeType,
  RuntimeItemId,
  type ThreadId,
  TurnId,
} from "@openswe/contracts";
import { getModelSelectionStringOptionValue } from "@openswe/shared/model";
import * as Cause from "effect/Cause";
import * as DateTime from "effect/DateTime";
import * as Effect from "effect/Effect";
import * as FileSystem from "effect/FileSystem";
import * as Fiber from "effect/Fiber";
import * as Path from "effect/Path";
import * as PubSub from "effect/PubSub";
import * as Result from "effect/Result";
import * as Scope from "effect/Scope";
import * as Semaphore from "effect/Semaphore";
import * as Stream from "effect/Stream";
import { HttpClient, HttpClientRequest } from "effect/unstable/http";

import { ProviderAdapterRequestError } from "../Errors.ts";
import { resolveAttachmentPath } from "../../attachmentStore.ts";
import { writeFileStringAtomically } from "../../atomicWrite.ts";
import type { ProviderAdapterShape } from "../Services/ProviderAdapter.ts";
import { langGraphAuthHeaders, langGraphBaseUrl } from "./LangGraphProvider.ts";

const DRIVER_KIND = ProviderDriverKind.make("langgraph");

interface LangGraphSessionState {
  readonly langGraphThreadId: string;
  readonly createdAt: string;
  readonly cwd: string | undefined;
  readonly runtimeMode: ProviderSession["runtimeMode"];
  model: string | undefined;
  effort: string | undefined;
  activeTurnId: TurnId | undefined;
  activeRunId: string | undefined;
  activeFiber: Fiber.Fiber<void, never> | undefined;
  readonly pendingTurns: Array<LangGraphPendingTurn>;
  lastError: string | undefined;
}

interface LangGraphPendingTurn {
  readonly threadId: ThreadId;
  readonly turnId: TurnId;
  readonly body: Record<string, unknown>;
  readonly model: string | undefined;
  readonly effort: string | undefined;
}

interface LangGraphResumeCursor {
  readonly threadId: string;
}

interface LangGraphAdapterDependencies {
  readonly attachmentsDir?: string;
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

function snapshotMessages(value: unknown): ReadonlyArray<Record<string, unknown>> {
  const state = asRecord(value);
  const values = asRecord(state?.["values"]);
  const messages = values?.["messages"];
  return Array.isArray(messages)
    ? messages.flatMap((message) => {
        const record = asRecord(message);
        if (record === undefined) return [];
        const type = record["type"];
        if (typeof type !== "string") return [];
        return [
          {
            type,
            ...(typeof record["id"] === "string" ? { id: record["id"] } : {}),
            content: record["content"],
            ...(Array.isArray(record["tool_calls"]) ? { tool_calls: record["tool_calls"] } : {}),
            ...(typeof record["tool_call_id"] === "string"
              ? { tool_call_id: record["tool_call_id"] }
              : {}),
            ...(typeof record["name"] === "string" ? { name: record["name"] } : {}),
          },
        ];
      })
    : [];
}

interface SseEvent {
  readonly event: string;
  readonly data: string;
}

/**
 * Decode an SSE byte stream into `{event, data}` records. Frames are
 * separated by a blank line; `data:` lines within one frame concatenate.
 */
function decodeSse<E, R>(bytes: Stream.Stream<Uint8Array, E, R>): Stream.Stream<SseEvent, E, R> {
  let event = "message";
  let data = "";
  return bytes.pipe(
    Stream.decodeText(),
    Stream.splitLines,
    Stream.filterMap((line) => {
      if (line === "") {
        if (data === "") {
          event = "message";
          return Result.failVoid;
        }
        const frame: SseEvent = { event, data };
        event = "message";
        data = "";
        return Result.succeed(frame);
      }
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        const chunk = line.slice(5).replace(/^ /, "");
        data = data === "" ? chunk : `${data}\n${chunk}`;
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
  ProviderAdapterShape<ProviderAdapterRequestError>,
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

    const baseUrl = langGraphBaseUrl(config);
    const headers = langGraphAuthHeaders(config);

    const nowIso = Effect.map(DateTime.now, DateTime.formatIso);
    let eventSeq = 0;
    let turnSeq = 0;
    const nextEventId = () => EventId.make(`langgraph-${String(Date.now())}-${String(++eventSeq)}`);

    const emit = (
      event: Omit<
        ProviderRuntimeEvent,
        "eventId" | "provider" | "providerInstanceId" | "createdAt"
      >,
    ): Effect.Effect<void> =>
      Effect.flatMap(nowIso, (createdAt) =>
        PubSub.publish(runtimeEvents, {
          ...event,
          eventId: nextEventId(),
          provider: DRIVER_KIND,
          providerInstanceId,
          createdAt,
        } as ProviderRuntimeEvent).pipe(Effect.asVoid),
      );

    const ensureDesktopProjectAllowed = (cwd: string | undefined) => {
      const allowlistPath = process.env.OPEN_SWE_LOCAL_PROJECTS_FILE?.trim();
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
          let entries: Array<unknown>;
          try {
            const parsed: unknown = JSON.parse(contents);
            if (!Array.isArray(parsed)) throw new Error("Expected a JSON array");
            entries = parsed;
          } catch (cause) {
            return yield* Effect.fail(
              failRequest(
                "startSession",
                "The Open SWE project allowlist is not a JSON array.",
                cause,
              ),
            );
          }

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

          yield* writeFileStringAtomically({
            filePath: projectsFile,
            contents: `${JSON.stringify([...entries, canonicalCwd], null, 2)}\n`,
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
          activeTurnId: undefined,
          activeRunId: undefined,
          activeFiber: undefined,
          pendingTurns: [],
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
    ) => {
      const emittedText = new Map<string, number>();
      const assistantItems = new Set<string>();
      const startedTools = new Set<string>();
      const completedTools = new Set<string>();
      const emittedUsage = new Set<string>();
      let streamedAssistantText = false;
      let failure: string | undefined;

      const handleMessage = (raw: unknown, fromUpdates = false): Effect.Effect<void> =>
        Effect.gen(function* () {
          const message = asRecord(raw);
          if (message === undefined) return;

          const type = message["type"];
          const id = typeof message["id"] === "string" ? message["id"] : undefined;

          if (type === "tool") {
            const toolCallId = message["tool_call_id"];
            if (typeof toolCallId !== "string") return;
            if (completedTools.has(toolCallId)) return;
            completedTools.add(toolCallId);
            const toolName = typeof message["name"] === "string" ? message["name"] : "tool";
            const itemType = classifyToolItemType(toolName);
            const isError = message["status"] === "error";
            yield* emit({
              type: "item.completed",
              threadId,
              turnId,
              itemId: RuntimeItemId.make(toolCallId),
              payload: {
                itemType,
                status: isError ? "failed" : "completed",
                title: titleForItemType(itemType, toolName),
                ...(summarize(message["content"]) === undefined
                  ? {}
                  : { detail: summarize(message["content"]) as string }),
                data: { toolCallId, kind: toolName, result: message["content"] },
              },
            });
            return;
          }

          if (type !== "ai" && type !== "AIMessageChunk") return;

          const usage = readUsage(message);
          const usageKey = JSON.stringify(usage);
          if (usage !== undefined && !emittedUsage.has(usageKey)) {
            emittedUsage.add(usageKey);
            yield* emit({
              type: "thread.token-usage.updated",
              threadId,
              payload: { usage },
            });
          }

          const text = extractText(message["content"]);
          if (id !== undefined && text.length > 0 && (!fromUpdates || !streamedAssistantText)) {
            if (!assistantItems.has(id)) {
              assistantItems.add(id);
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
            const delta = type === "AIMessageChunk" ? text : text.slice(already);
            if (delta.length > 0) {
              if (!fromUpdates) streamedAssistantText = true;
              emittedText.set(id, already + delta.length);
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
            if (typeof toolCallId !== "string" || toolName === undefined) continue;
            if (startedTools.has(toolCallId)) continue;
            startedTools.add(toolCallId);
            const itemType = classifyToolItemType(toolName);
            yield* emit({
              type: "item.started",
              threadId,
              turnId,
              itemId: RuntimeItemId.make(toolCallId),
              payload: {
                itemType,
                status: "inProgress",
                title: titleForItemType(itemType, toolName),
                ...(summarize(record["args"]) === undefined
                  ? {}
                  : { detail: summarize(record["args"]) as string }),
                data: { toolCallId, kind: toolName, input: record["args"] },
              },
            });
          }
        });

      const handleFrame = (frame: SseEvent): Effect.Effect<void> =>
        Effect.gen(function* () {
          let parsed: unknown;
          try {
            parsed = JSON.parse(frame.data);
          } catch {
            return;
          }

          // The run id needed to cancel arrives once, up front, in the
          // metadata frame — not on the data frames.
          if (frame.event === "metadata") {
            const runId = asRecord(parsed)?.["run_id"];
            if (typeof runId === "string") session.activeRunId = runId;
            return;
          }

          if (frame.event === "error") {
            failure =
              summarize(asRecord(parsed)?.["message"] ?? parsed) ?? "The Open SWE run failed.";
            return;
          }

          if (frame.event.startsWith("messages")) {
            // messages-tuple frames are [message, metadata]; some servers
            // send the bare message instead.
            const message = Array.isArray(parsed) ? parsed[0] : parsed;
            yield* handleMessage(message);
            return;
          }

          if (frame.event === "updates" || frame.event === "values") {
            const record = asRecord(parsed);
            if (record === undefined) return;
            for (const value of Object.values(record)) {
              const messages = asRecord(value)?.["messages"];
              if (!Array.isArray(messages)) continue;
              for (const message of messages) {
                yield* handleMessage(message, true);
              }
            }
          }
        });

      return decodeSse(response.stream).pipe(
        Stream.runForEach(handleFrame),
        Effect.catchCause((cause) =>
          Effect.sync(() => {
            failure = Cause.pretty(cause);
          }),
        ),
        Effect.andThen(() =>
          Effect.gen(function* () {
            if (session.activeTurnId !== turnId) return;
            for (const itemId of assistantItems) {
              yield* emit({
                type: "item.completed",
                threadId,
                turnId,
                itemId: RuntimeItemId.make(itemId),
                payload: {
                  itemType: "assistant_message",
                  status: failure === undefined ? "completed" : "failed",
                },
              });
            }
            session.lastError = failure;
            session.activeTurnId = undefined;
            session.activeRunId = undefined;
            session.activeFiber = undefined;
            yield* emit({
              type: "turn.completed",
              threadId,
              turnId,
              payload:
                failure === undefined
                  ? { state: "completed" }
                  : { state: "failed", errorMessage: failure },
            });
            if (failure !== undefined) {
              yield* emit({
                type: "runtime.error",
                threadId,
                turnId,
                payload: { message: failure },
              });
            }
            yield* emit({
              type: "session.state.changed",
              threadId,
              payload:
                failure === undefined
                  ? { state: "ready", reason: "Open SWE turn completed" }
                  : { state: "error", reason: failure },
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
        session.lastError = undefined;
        const fiber = yield* consumeRun(pending.threadId, pending.turnId, session, response).pipe(
          Effect.forkIn(providerScope),
        );
        if (session.activeTurnId === pending.turnId) session.activeFiber = fiber;
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

    const sendTurn: ProviderAdapterShape<ProviderAdapterRequestError>["sendTurn"] = (input) =>
      Effect.gen(function* () {
        const session = yield* requireSession(input.threadId, "sendTurn");
        const model = input.modelSelection?.model ?? session.model;
        const effort =
          getModelSelectionStringOptionValue(input.modelSelection, "effort") ?? session.effort;
        session.model = model;
        session.effort = effort;

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

        const turnId = TurnId.make(`langgraph-turn-${String(Date.now())}-${String(++turnSeq)}`);
        const runBody: Record<string, unknown> = {
          assistant_id: config.graphId,
          input: { messages: [{ role: "user", content }] },
          stream_mode: ["messages-tuple", "updates"],
          config: {
            configurable: {
              source: "desktop",
              ...(session.cwd === undefined ? {} : { local_project_path: session.cwd }),
              ...(model === undefined ? {} : { agent_model_id: model }),
              ...(effort === undefined ? {} : { agent_effort: effort }),
              plan_mode: input.interactionMode === "plan",
            },
          },
        };

        const pending = { threadId: input.threadId, turnId, body: runBody, model, effort };
        if (session.activeTurnId === undefined) {
          yield* startTurn(session, pending);
        } else {
          session.pendingTurns.push(pending);
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

    const stopSession: ProviderAdapterShape<ProviderAdapterRequestError>["stopSession"] = (
      threadId,
    ) =>
      Effect.gen(function* () {
        const session = sessions.get(threadId);
        if (session === undefined) return;
        session.activeTurnId = undefined;
        session.activeRunId = undefined;
        if (session.activeFiber !== undefined) {
          yield* Fiber.interrupt(session.activeFiber);
        }
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
      });

    return {
      provider: DRIVER_KIND,
      capabilities: { sessionModelSwitch: "in-session" },
      startSession,
      sendTurn,
      interruptTurn,
      respondToRequest: () =>
        Effect.fail(
          failRequest(
            "respondToRequest",
            "Open SWE runs without interactive approvals; there is nothing to respond to.",
          ),
        ),
      respondToUserInput: () =>
        Effect.fail(
          failRequest("respondToUserInput", "Open SWE does not request structured user input."),
        ),
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
      rollbackThread: (threadId) =>
        Effect.fail(
          failRequest(
            "rollbackThread",
            `Open SWE cannot safely roll back LangGraph thread ${String(threadId)} checkpoints.`,
          ),
        ),
      stopAll: () =>
        Effect.forEach([...sessions.keys()], stopSession, { discard: true }).pipe(Effect.asVoid),
      streamEvents: Stream.fromPubSub(runtimeEvents),
    } satisfies ProviderAdapterShape<ProviderAdapterRequestError>;
  });
}
