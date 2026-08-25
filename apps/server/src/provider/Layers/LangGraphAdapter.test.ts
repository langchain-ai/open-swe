import * as NodeServices from "@effect/platform-node/NodeServices";
import {
  ApprovalRequestId,
  LangGraphSettings,
  ProviderDriverKind,
  ProviderInstanceId,
  type ProviderRuntimeEvent,
  ThreadId,
} from "@openswe/contracts";
import { assert, describe, it } from "@effect/vitest";
import * as Deferred from "effect/Deferred";
import * as Effect from "effect/Effect";
import * as Fiber from "effect/Fiber";
import * as FileSystem from "effect/FileSystem";
import * as Schema from "effect/Schema";
import * as Stream from "effect/Stream";
import { HttpClient, HttpClientResponse } from "effect/unstable/http";

import { makeLangGraphAdapter } from "./LangGraphAdapter.ts";

const decodeSettings = Schema.decodeSync(LangGraphSettings);
const decodeJson = Schema.decodeUnknownSync(Schema.fromJsonString(Schema.Unknown));
const encodeJson = Schema.encodeUnknownSync(Schema.fromJsonString(Schema.Unknown));
const instanceId = ProviderInstanceId.make("open-swe-test");
const threadId = ThreadId.make("thread-open-swe-test");
const encoder = new TextEncoder();

function requestJson(request: { readonly body: unknown }): unknown {
  const body = request.body as { readonly _tag?: string; readonly body?: Uint8Array };
  return body._tag === "Uint8Array" && body.body !== undefined
    ? decodeJson(new TextDecoder().decode(body.body))
    : undefined;
}

function sseResponse(request: Parameters<typeof HttpClientResponse.fromWeb>[0], frames: string) {
  return HttpClientResponse.fromWeb(
    request,
    new Response(encoder.encode(frames), {
      headers: { "content-type": "text/event-stream" },
    }),
  );
}

describe("LangGraphAdapter", () => {
  it.effect("streams a complete turn after sendTurn returns and forwards desktop config", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const requests: Array<{
          url: string;
          authorization: string | undefined;
          body: unknown;
        }> = [];
        const nativeEvents: unknown[] = [];
        const client = HttpClient.make((request) => {
          requests.push({
            url: request.url,
            authorization: request.headers["authorization"],
            body: requestJson(request),
          });
          if (request.url.endsWith("/threads")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ thread_id: String(threadId) })),
            );
          }
          if (request.url.endsWith("/runs/run-1")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ status: "success" })),
            );
          }
          return Effect.succeed(
            sseResponse(
              request,
              [
                'event: metadata\ndata: {"run_id":"run-1"}\n',
                'event: messages-tuple\ndata: [{"type":"AIMessageChunk","id":"chunk-1","content":"hello"},{}]\n',
                'event: messages-tuple\ndata: [{"type":"AIMessageChunk","id":"chunk-1","content":" world"},{}]\n',
                'event: updates\ndata: {"agent":{"messages":[{"type":"ai","id":"response-1","content":"hello world"}]}}\n',
                "",
              ].join("\n"),
            ),
          );
        });
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test", graphId: "agent" }),
          instanceId,
          {
            environment: { OPEN_SWE_LOCAL_AUTH_TOKEN: "adapter-placeholder" },
            nativeEventLogger: {
              filePath: "memory",
              write: (event) => Effect.sync(() => nativeEvents.push(event)),
              close: () => Effect.void,
            },
          },
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        const events: ProviderRuntimeEvent[] = [];
        const completed = yield* Deferred.make<void>();
        const eventFiber = yield* Stream.runForEach(adapter.streamEvents, (event) =>
          Effect.sync(() => events.push(event)).pipe(
            Effect.andThen(
              event.type === "turn.completed"
                ? Deferred.succeed(completed, undefined)
                : Effect.void,
            ),
          ),
        ).pipe(Effect.forkChild);
        yield* Effect.yieldNow;

        const session = yield* adapter.startSession({
          threadId,
          provider: ProviderDriverKind.make("langgraph"),
          cwd: "/trusted/project",
          runtimeMode: "full-access",
          modelSelection: { instanceId, model: "openai:gpt-5.6-terra" },
        });
        const started = yield* adapter.sendTurn({ threadId, input: "hello world" });
        yield* Deferred.await(completed);
        yield* Fiber.interrupt(eventFiber);

        assert.deepStrictEqual(session.resumeCursor, { threadId: String(threadId) });
        assert.deepStrictEqual(started.resumeCursor, { threadId: String(threadId) });
        assert.deepInclude(requests[0]?.body, {
          thread_id: String(threadId),
          if_exists: "do_nothing",
        });
        assert.isTrue(
          requests.every((request) => request.authorization === "Bearer adapter-placeholder"),
        );
        assert.includeMembers(
          events.map((event) => event.type),
          [
            "session.started",
            "session.state.changed",
            "thread.started",
            "turn.started",
            "item.started",
            "content.delta",
            "item.completed",
            "turn.completed",
          ],
        );
        assert.isTrue(events.every((event) => event.providerInstanceId === instanceId));
        assert.equal(
          events.find((event) => event.type === "turn.completed")?.payload.state,
          "completed",
        );
        assert.equal(
          events
            .filter((event) => event.type === "content.delta")
            .map((event) => (event.type === "content.delta" ? event.payload.delta : ""))
            .join(""),
          "hello world",
        );
        const runBody = requests.find((entry) => entry.url.endsWith("/runs/stream"))?.body as {
          stream_resumable?: boolean;
          config?: { configurable?: Record<string, unknown> };
        };
        assert.isTrue(runBody.stream_resumable);
        assert.deepInclude(runBody.config?.configurable, {
          source: "desktop",
          local_project_path: "/trusted/project",
          agent_model_id: "openai:gpt-5.6-terra",
        });
        assert.isAbove(nativeEvents.length, 0);
        assert.notInclude(encodeJson(nativeEvents), "adapter-placeholder");
        assert.isTrue(
          events.some(
            (event) => event.raw?.source === "langgraph.sse" && event.raw.method === "metadata",
          ),
        );
        assert.isUndefined(events.find((event) => event.type === "session.started")?.raw);
        assert.isUndefined(events.find((event) => event.type === "turn.completed")?.raw);
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("maps protocol-v2 reasoning, tools, tasks, plans, diffs, and thread state", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const requests: Array<{ url: string; body: unknown }> = [];
        const frame = (event: string, data: unknown) =>
          `event: ${event}\ndata: ${encodeJson(data)}\n`;
        const client = HttpClient.make((request) => {
          requests.push({ url: request.url, body: requestJson(request) });
          if (request.url.endsWith("/threads")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-v2" })),
            );
          }
          return Effect.succeed(
            sseResponse(
              request,
              [
                frame("metadata", { run_id: "run-v2" }),
                frame("lifecycle", { event: "running", graph_name: "agent" }),
                frame("messages", {
                  event: "message-start",
                  role: "ai",
                  id: "message-v2",
                  metadata: { model: "gpt-test" },
                }),
                frame("messages", {
                  event: "content-block-delta",
                  index: 0,
                  delta: { type: "reasoning-delta", reasoning: "thinking" },
                }),
                frame("messages", {
                  event: "content-block-delta",
                  index: 1,
                  delta: { type: "text-delta", text: "answer" },
                }),
                frame("tools", {
                  event: "tool-started",
                  tool_call_id: "execute-1",
                  tool_name: "execute",
                  input: { command: "pwd" },
                }),
                frame("tools", {
                  event: "tool-output-delta",
                  tool_call_id: "execute-1",
                  delta: "/workspace\n",
                }),
                frame("tools", {
                  event: "tool-finished",
                  tool_call_id: "execute-1",
                  output: "ok",
                }),
                frame("tools", {
                  event: "tool-started",
                  tool_call_id: "task-1",
                  tool_name: "task",
                  input: { description: "Inspect tests", subagent_type: "general-purpose" },
                }),
                frame("lifecycle|agent:task-1", {
                  event: "started",
                  graph_name: "general-purpose",
                  cause: { type: "toolCall", tool_call_id: "task-1" },
                }),
                frame("tools|agent:task-1", {
                  event: "tool-started",
                  tool_call_id: "child-tool",
                  tool_name: "execute",
                  input: { command: "rg test" },
                }),
                frame("lifecycle|agent:task-1", {
                  event: "completed",
                  graph_name: "general-purpose",
                }),
                frame("tools", {
                  event: "tool-finished",
                  tool_call_id: "task-1",
                  output: "Tests inspected",
                }),
                frame("custom", {
                  name: "turn.proposed.delta",
                  payload: { delta: "Draft plan" },
                }),
                frame("custom", {
                  name: "turn.proposed.completed",
                  payload: { planMarkdown: "# Plan" },
                }),
                frame("updates", {
                  agent: {
                    todos: [
                      { content: "Inspect", status: "completed" },
                      { content: "Implement", status: "in_progress" },
                    ],
                    unified_diff: "diff --git a/a b/a",
                  },
                }),
                frame("messages", { event: "message-finish" }),
                frame("lifecycle", { event: "completed", graph_name: "agent" }),
                "",
              ].join("\n"),
            ),
          );
        });
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test" }),
          instanceId,
          {
            nativeEventLogger: {
              filePath: "memory",
              write: () => Effect.die(new Error("native logging failed")),
              close: () => Effect.void,
            },
          },
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        const events: ProviderRuntimeEvent[] = [];
        const completed = yield* Deferred.make<void>();
        const eventFiber = yield* Stream.runForEach(adapter.streamEvents, (event) =>
          Effect.sync(() => events.push(event)).pipe(
            Effect.andThen(
              event.type === "turn.completed"
                ? Deferred.succeed(completed, undefined)
                : Effect.void,
            ),
          ),
        ).pipe(Effect.forkChild);
        yield* Effect.yieldNow;
        yield* adapter.startSession({ threadId, runtimeMode: "full-access" });
        yield* adapter.sendTurn({ threadId, input: "exercise v2" });
        yield* Deferred.await(completed);
        yield* Fiber.interrupt(eventFiber);

        assert.includeMembers(
          events.map((event) => event.type),
          [
            "thread.metadata.updated",
            "thread.state.changed",
            "item.updated",
            "task.started",
            "task.updated",
            "task.completed",
            "turn.plan.updated",
            "turn.diff.updated",
            "turn.proposed.delta",
            "turn.proposed.completed",
          ],
        );
        assert.isTrue(
          events.some(
            (event) =>
              event.type === "content.delta" &&
              event.payload.streamKind === "reasoning_text" &&
              event.payload.delta === "thinking",
          ),
        );
        assert.isTrue(
          events.some(
            (event) =>
              event.type === "content.delta" &&
              event.payload.streamKind === "command_output" &&
              event.payload.delta === "/workspace\n",
          ),
        );
        const childTool = events.find(
          (event) => event.type === "item.started" && event.itemId === "child-tool",
        );
        assert.equal(childTool?.type, "item.started");
        if (childTool?.type === "item.started") assert.equal(childTool.payload.agentId, "task-1");
        const runBody = requests.find((entry) => entry.url.endsWith("/runs/stream"))?.body as {
          stream_mode?: ReadonlyArray<string>;
          stream_subgraphs?: boolean;
          config?: { configurable?: Record<string, unknown> };
        };
        assert.includeMembers([...(runBody.stream_mode ?? [])], ["messages", "custom", "tasks"]);
        assert.isTrue(runBody.stream_subgraphs);
        assert.isTrue(runBody.config?.configurable?.["__event_streaming_v2"]);
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("rejects model selections bound to a different provider instance", () =>
    Effect.scoped(
      Effect.gen(function* () {
        let runRequests = 0;
        const client = HttpClient.make((request) => {
          if (request.url.endsWith("/threads")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-bound" })),
            );
          }
          runRequests += 1;
          return Effect.succeed(sseResponse(request, "event: values\ndata: {}\n\n"));
        });
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test" }),
          instanceId,
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        yield* adapter.startSession({ threadId, runtimeMode: "full-access" });

        const error = yield* adapter
          .sendTurn({
            threadId,
            input: "wrong instance",
            modelSelection: {
              instanceId: ProviderInstanceId.make("different-instance"),
              model: "openai:gpt-5.6-sol",
            },
          })
          .pipe(Effect.flip);

        assert.equal(error._tag, "ProviderAdapterValidationError");
        assert.equal(runRequests, 0);
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("fails closed on malformed SSE JSON without echoing the frame", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const client = HttpClient.make((request) =>
          Effect.succeed(
            request.url.endsWith("/threads")
              ? HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-malformed" }))
              : sseResponse(request, "event: updates\ndata: {private-invalid-json\n\n"),
          ),
        );
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test" }),
          instanceId,
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        const completed = yield* Deferred.make<ProviderRuntimeEvent>();
        const eventFiber = yield* Stream.runForEach(adapter.streamEvents, (event) =>
          event.type === "turn.completed" ? Deferred.succeed(completed, event) : Effect.void,
        ).pipe(Effect.forkChild);

        yield* adapter.startSession({ threadId, runtimeMode: "full-access" });
        yield* adapter.sendTurn({ threadId, input: "malformed" });
        const event = yield* Deferred.await(completed);
        yield* Fiber.interrupt(eventFiber);
        assert.equal(event.type, "turn.completed");
        if (event.type === "turn.completed") {
          assert.equal(event.payload.state, "failed");
          assert.equal(
            event.payload.errorMessage,
            "The Open SWE run returned a malformed event stream frame.",
          );
          assert.notInclude(event.payload.errorMessage ?? "", "private-invalid-json");
        }
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("reconnects a still-running durable run from the last SSE event id", () =>
    Effect.scoped(
      Effect.gen(function* () {
        let statusChecks = 0;
        let reconnectCursor: string | undefined;
        const client = HttpClient.make((request) => {
          if (request.url.endsWith("/threads")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-reconnect" })),
            );
          }
          if (request.url.endsWith("/runs/run-reconnect/stream")) {
            reconnectCursor = request.headers["last-event-id"];
            return Effect.succeed(
              sseResponse(
                request,
                'id: 42\nevent: messages-tuple\ndata: [{"type":"AIMessageChunk","id":"after-reconnect","content":"recovered"},{}]\n\n',
              ),
            );
          }
          if (request.url.endsWith("/runs/run-reconnect")) {
            statusChecks += 1;
            return Effect.succeed(
              HttpClientResponse.fromWeb(
                request,
                Response.json({ status: statusChecks === 1 ? "running" : "success" }),
              ),
            );
          }
          return Effect.succeed(
            sseResponse(request, 'id: 41\nevent: metadata\ndata: {"run_id":"run-reconnect"}\n\n'),
          );
        });
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test" }),
          instanceId,
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        const events: ProviderRuntimeEvent[] = [];
        const completed = yield* Deferred.make<void>();
        const eventFiber = yield* Stream.runForEach(adapter.streamEvents, (event) =>
          Effect.sync(() => events.push(event)).pipe(
            Effect.andThen(
              event.type === "turn.completed"
                ? Deferred.succeed(completed, undefined)
                : Effect.void,
            ),
          ),
        ).pipe(Effect.forkChild);

        yield* adapter.startSession({ threadId, runtimeMode: "full-access" });
        yield* adapter.sendTurn({ threadId, input: "recover" });
        yield* Deferred.await(completed);
        yield* Fiber.interrupt(eventFiber);

        assert.equal(reconnectCursor, "41");
        assert.equal(statusChecks, 2);
        assert.equal(
          events
            .filter((event) => event.type === "content.delta")
            .map((event) => (event.type === "content.delta" ? event.payload.delta : ""))
            .join(""),
          "recovered",
        );
        assert.equal(
          events.find((event) => event.type === "turn.completed")?.payload.state,
          "completed",
        );
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("uses terminal durable run errors when a clean stream closes early", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const client = HttpClient.make((request) => {
          if (request.url.endsWith("/threads")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-run-error" })),
            );
          }
          if (request.url.endsWith("/runs/run-error")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(
                request,
                Response.json({ status: "error", error: "provider unavailable" }),
              ),
            );
          }
          return Effect.succeed(
            sseResponse(request, 'event: metadata\ndata: {"run_id":"run-error"}\n\n'),
          );
        });
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test" }),
          instanceId,
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        const completed = yield* Deferred.make<ProviderRuntimeEvent>();
        const eventFiber = yield* Stream.runForEach(adapter.streamEvents, (event) =>
          event.type === "turn.completed" ? Deferred.succeed(completed, event) : Effect.void,
        ).pipe(Effect.forkChild);

        yield* adapter.startSession({ threadId, runtimeMode: "full-access" });
        yield* adapter.sendTurn({ threadId, input: "fail" });
        const event = yield* Deferred.await(completed);
        yield* Fiber.interrupt(eventFiber);
        assert.equal(event.type, "turn.completed");
        if (event.type === "turn.completed") {
          assert.equal(event.payload.state, "failed");
          assert.include(event.payload.errorMessage ?? "", "provider unavailable");
        }
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("settles an SSE error as a failed turn", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const client = HttpClient.make((request) =>
          Effect.succeed(
            request.url.endsWith("/threads")
              ? HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-thread" }))
              : sseResponse(
                  request,
                  'event: error\ndata: {"message":"gateway rejected model"}\n\n',
                ),
          ),
        );
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test" }),
          instanceId,
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        const completed = yield* Deferred.make<ProviderRuntimeEvent>();
        const eventFiber = yield* Stream.runForEach(adapter.streamEvents, (event) =>
          event.type === "turn.completed" ? Deferred.succeed(completed, event) : Effect.void,
        ).pipe(Effect.forkChild);
        yield* adapter.startSession({ threadId, runtimeMode: "full-access" });
        yield* adapter.sendTurn({ threadId, input: "hello" });
        const event = yield* Deferred.await(completed);
        yield* Fiber.interrupt(eventFiber);
        assert.equal(event.type, "turn.completed");
        if (event.type === "turn.completed") {
          assert.equal(event.payload.state, "failed");
          assert.equal(event.payload.errorMessage, "gateway rejected model");
        }
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("forwards effort, plan mode, images, usage, and hydrates thread history", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const fs = yield* FileSystem.FileSystem;
        const attachmentsDir = yield* fs.makeTempDirectoryScoped({ prefix: "open-swe-images-" });
        const attachmentId = "thread-open-swe-test-00000000-0000-4000-8000-000000000001";
        yield* fs.writeFile(
          `${attachmentsDir}/${attachmentId}.png`,
          new TextEncoder().encode("pixels"),
        );
        const requests: Array<{ url: string; body: unknown }> = [];
        const client = HttpClient.make((request) => {
          requests.push({ url: request.url, body: requestJson(request) });
          if (request.url.endsWith("/threads")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-history" })),
            );
          }
          if (request.url.endsWith("/state")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(
                request,
                Response.json({
                  values: {
                    messages: [
                      { type: "human", id: "human-1", content: "first" },
                      { type: "ai", id: "ai-1", content: "done" },
                      { type: "human", id: "human-2", content: "second" },
                      { type: "tool", tool_call_id: "tool-1", content: "ok" },
                    ],
                  },
                }),
              ),
            );
          }
          return Effect.succeed(
            sseResponse(
              request,
              [
                'event: messages-tuple\ndata: [{"type":"AIMessageChunk","id":"ai-usage","content":"done","usage_metadata":{"input_tokens":7,"output_tokens":3,"total_tokens":10,"input_token_details":{"cache_read":2}}},{}]\n',
                'event: updates\ndata: {"agent":{"messages":[{"type":"ai","id":"ai-usage","content":"done","usage_metadata":{"input_tokens":7,"output_tokens":3,"total_tokens":10,"input_token_details":{"cache_read":2}}}]}}\n',
                "",
              ].join("\n"),
            ),
          );
        });
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test", graphId: "agent" }),
          instanceId,
          { attachmentsDir },
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        const events: ProviderRuntimeEvent[] = [];
        const completed = yield* Deferred.make<void>();
        const eventFiber = yield* Stream.runForEach(adapter.streamEvents, (event) =>
          Effect.sync(() => events.push(event)).pipe(
            Effect.andThen(
              event.type === "turn.completed"
                ? Deferred.succeed(completed, undefined)
                : Effect.void,
            ),
          ),
        ).pipe(Effect.forkChild);
        yield* Effect.yieldNow;

        yield* adapter.startSession({ threadId, runtimeMode: "full-access" });
        yield* adapter.sendTurn({
          threadId,
          input: "inspect this",
          interactionMode: "plan",
          modelSelection: {
            instanceId,
            model: "openai:gpt-5.6-sol",
            options: [{ id: "effort", value: "high" }],
          },
          attachments: [
            {
              type: "image",
              id: attachmentId,
              name: "screen.png",
              mimeType: "image/png",
              sizeBytes: 6,
            },
          ],
        });
        yield* Deferred.await(completed);
        const snapshot = yield* adapter.readThread(threadId);
        yield* Fiber.interrupt(eventFiber);

        const runBody = requests.find((entry) => entry.url.endsWith("/runs/stream"))?.body as {
          input?: { messages?: Array<{ content?: unknown }> };
          config?: { configurable?: Record<string, unknown> };
        };
        assert.deepInclude(runBody.config?.configurable, {
          agent_model_id: "openai:gpt-5.6-sol",
          agent_effort: "high",
          plan_mode: true,
        });
        assert.deepStrictEqual(runBody.input?.messages?.[0]?.content, [
          { type: "text", text: "inspect this" },
          { type: "image_url", image_url: { url: "data:image/png;base64,cGl4ZWxz" } },
        ]);
        const usageEvents = events.filter((event) => event.type === "thread.token-usage.updated");
        assert.lengthOf(usageEvents, 1);
        const usageEvent = usageEvents[0];
        assert.equal(usageEvent?.type, "thread.token-usage.updated");
        if (usageEvent?.type === "thread.token-usage.updated") {
          assert.equal(usageEvent.payload.usage.usedTokens, 10);
          assert.equal(usageEvent.payload.usage.inputTokens, 7);
          assert.equal(usageEvent.payload.usage.cachedInputTokens, 2);
          assert.equal(usageEvent.payload.usage.outputTokens, 3);
        }
        assert.equal(snapshot.turns.length, 2);
        assert.equal(snapshot.turns[0]?.id, "human-1");
        assert.equal(snapshot.turns[0]?.items.length, 2);
        assert.equal(snapshot.turns[1]?.id, "human-2");
        assert.equal(snapshot.turns[1]?.items.length, 2);
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("cancels the run id received in the metadata frame", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const requestUrls: string[] = [];
        const client = HttpClient.make((request) => {
          requestUrls.push(request.url);
          if (request.url.endsWith("/threads")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-thread" })),
            );
          }
          if (request.url.endsWith("/runs/stream")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(
                request,
                new Response(
                  new ReadableStream<Uint8Array>({
                    start(controller) {
                      controller.enqueue(
                        encoder.encode('event: metadata\ndata: {"run_id":"run-cancel"}\n\n'),
                      );
                    },
                    cancel() {},
                  }),
                  { headers: { "content-type": "text/event-stream" } },
                ),
              ),
            );
          }
          return Effect.succeed(HttpClientResponse.fromWeb(request, Response.json({})));
        });
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test" }),
          instanceId,
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        const aborted = yield* Deferred.make<void>();
        const eventFiber = yield* Stream.runForEach(adapter.streamEvents, (event) =>
          event.type === "turn.aborted" ? Deferred.succeed(aborted, undefined) : Effect.void,
        ).pipe(Effect.forkChild);
        yield* Effect.yieldNow;
        yield* adapter.startSession({ threadId, runtimeMode: "full-access" });
        yield* adapter.sendTurn({ threadId, input: "hello" });
        yield* Effect.yieldNow;
        yield* Effect.yieldNow;
        yield* adapter.interruptTurn(threadId);
        yield* Deferred.await(aborted);
        yield* Fiber.interrupt(eventFiber);
        assert.isTrue(requestUrls.some((url) => url.endsWith("/runs/run-cancel/cancel")));
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("interrupts the active LangGraph run and keeps steering in the same turn", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const runBodies: unknown[] = [];
        let runRequests = 0;
        const client = HttpClient.make((request) => {
          if (request.url.endsWith("/threads")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-queue" })),
            );
          }
          runBodies.push(requestJson(request));
          runRequests += 1;
          if (runRequests === 1) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(
                request,
                new Response(
                  new ReadableStream<Uint8Array>({
                    start() {},
                    cancel() {},
                  }),
                  { headers: { "content-type": "text/event-stream" } },
                ),
              ),
            );
          }
          return Effect.succeed(sseResponse(request, "event: values\ndata: {}\n\n"));
        });
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test" }),
          instanceId,
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        const completed = yield* Deferred.make<void>();
        const events: ProviderRuntimeEvent[] = [];
        const eventFiber = yield* Stream.runForEach(adapter.streamEvents, (event) =>
          Effect.sync(() => events.push(event)).pipe(
            Effect.andThen(
              event.type === "turn.completed"
                ? Deferred.succeed(completed, undefined)
                : Effect.void,
            ),
          ),
        ).pipe(Effect.forkChild);
        yield* Effect.yieldNow;
        yield* adapter.startSession({ threadId, runtimeMode: "full-access" });
        const first = yield* adapter.sendTurn({ threadId, input: "first" });
        const second = yield* adapter.sendTurn({ threadId, input: "steer next" });
        assert.equal(second.turnId, first.turnId);
        assert.equal(runRequests, 2);
        assert.deepInclude(runBodies[1] as Record<string, unknown>, {
          multitask_strategy: "interrupt",
        });

        yield* Deferred.await(completed);
        yield* Fiber.interrupt(eventFiber);
        assert.lengthOf(
          events.filter((event) => event.type === "turn.started"),
          1,
        );
        assert.lengthOf(
          events.filter((event) => event.type === "turn.completed"),
          1,
        );
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("preserves plan mode when omitted and resets it on explicit default mode", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const runBodies: Array<{ config?: { configurable?: Record<string, unknown> } }> = [];
        const client = HttpClient.make((request) => {
          if (request.url.endsWith("/threads")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-plan-mode" })),
            );
          }
          runBodies.push(
            requestJson(request) as { config?: { configurable?: Record<string, unknown> } },
          );
          return Effect.succeed(sseResponse(request, "event: values\ndata: {}\n\n"));
        });
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test" }),
          instanceId,
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        const completions = [
          yield* Deferred.make<void>(),
          yield* Deferred.make<void>(),
          yield* Deferred.make<void>(),
        ];
        let completionIndex = 0;
        const eventFiber = yield* Stream.runForEach(adapter.streamEvents, (event) =>
          event.type === "turn.completed"
            ? Deferred.succeed(completions[completionIndex++]!, undefined)
            : Effect.void,
        ).pipe(Effect.forkChild);
        yield* Effect.yieldNow;

        yield* adapter.startSession({ threadId, runtimeMode: "full-access" });
        yield* adapter.sendTurn({ threadId, input: "plan", interactionMode: "plan" });
        yield* Deferred.await(completions[0]!);
        yield* adapter.sendTurn({ threadId, input: "still plan" });
        yield* Deferred.await(completions[1]!);
        yield* adapter.sendTurn({ threadId, input: "exit plan", interactionMode: "default" });
        yield* Deferred.await(completions[2]!);
        yield* Fiber.interrupt(eventFiber);

        assert.deepEqual(
          runBodies.map((body) => body.config?.configurable?.["plan_mode"]),
          [true, true, false],
        );
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("surfaces LangGraph HITL requests and resumes the active turn", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const requests: Array<{ url: string; body: unknown }> = [];
        let runRequest = 0;
        const client = HttpClient.make((request) => {
          requests.push({ url: request.url, body: requestJson(request) });
          if (request.url.endsWith("/threads")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-hitl" })),
            );
          }
          runRequest += 1;
          return Effect.succeed(
            runRequest === 1
              ? sseResponse(
                  request,
                  [
                    'event: metadata\ndata: {"run_id":"run-hitl"}\n',
                    'event: updates\ndata: {"__interrupt__":[{"id":"interrupt-1","value":{"action_requests":[{"name":"execute","args":{"command":"git status"},"description":"Run command"}],"review_configs":[{"action_name":"execute","allowed_decisions":["approve","reject"]}]}}]}\n',
                    "",
                  ].join("\n"),
                )
              : sseResponse(
                  request,
                  'event: messages-tuple\ndata: [{"type":"AIMessageChunk","id":"after-hitl","content":"done"},{}]\n\n',
                ),
          );
        });
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test", graphId: "agent" }),
          instanceId,
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        const opened = yield* Deferred.make<ProviderRuntimeEvent>();
        const completed = yield* Deferred.make<void>();
        const eventFiber = yield* Stream.runForEach(adapter.streamEvents, (event) =>
          Effect.all(
            [
              event.type === "request.opened" ? Deferred.succeed(opened, event) : Effect.void,
              event.type === "turn.completed"
                ? Deferred.succeed(completed, undefined)
                : Effect.void,
            ],
            { discard: true },
          ),
        ).pipe(Effect.forkChild);
        yield* Effect.yieldNow;

        yield* adapter.startSession({ threadId, runtimeMode: "approval-required" });
        yield* adapter.sendTurn({ threadId, input: "check status" });
        const requestEvent = yield* Deferred.await(opened);
        assert.equal(requestEvent.type, "request.opened");
        if (requestEvent.type === "request.opened") {
          assert.equal(requestEvent.payload.requestType, "command_execution_approval");
          assert.equal(requestEvent.requestId, "interrupt-1");
        }

        yield* adapter.respondToRequest(threadId, ApprovalRequestId.make("interrupt-1"), "accept");
        yield* Deferred.await(completed);
        yield* Fiber.interrupt(eventFiber);

        const runBodies = requests
          .filter((entry) => entry.url.endsWith("/runs/stream"))
          .map((entry) => entry.body);
        const initialBody = runBodies[0] as {
          config?: { configurable?: Record<string, unknown> };
        };
        assert.equal(initialBody.config?.configurable?.["runtime_mode"], "approval-required");
        const resumeBody = runBodies[1] as { command?: { resume?: Record<string, unknown> } };
        assert.deepStrictEqual(resumeBody.command?.resume, {
          "interrupt-1": { decisions: [{ type: "approve" }] },
        });
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("offers only backend-supported approval decisions", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const runBodies: Array<unknown> = [];
        let runRequest = 0;
        const client = HttpClient.make((request) => {
          if (request.url.endsWith("/threads")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-gated-hitl" })),
            );
          }
          runBodies.push(requestJson(request));
          runRequest += 1;
          return Effect.succeed(
            sseResponse(
              request,
              runRequest === 1
                ? 'event: updates\ndata: {"__interrupt__":[{"id":"interrupt-gated","value":{"action_requests":[{"name":"execute","args":{"command":"git status"}}],"review_configs":[{"action_name":"execute","allowed_decisions":["approve","reject"]}]}}]}\n\n'
                : "event: values\ndata: {}\n\n",
            ),
          );
        });
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test" }),
          instanceId,
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        const opened = yield* Deferred.make<ProviderRuntimeEvent>();
        const completed = yield* Deferred.make<void>();
        const eventFiber = yield* Stream.runForEach(adapter.streamEvents, (event) => {
          if (event.type === "request.opened") {
            return Deferred.succeed(opened, event);
          }
          if (event.type === "turn.completed") {
            return Deferred.succeed(completed, undefined);
          }
          return Effect.void;
        }).pipe(Effect.forkChild);
        yield* Effect.yieldNow;

        yield* adapter.startSession({ threadId, runtimeMode: "approval-required" });
        yield* adapter.sendTurn({ threadId, input: "first" });
        const openedEvent = yield* Deferred.await(opened);
        assert.equal(openedEvent.type, "request.opened");
        if (openedEvent.type === "request.opened") {
          assert.deepStrictEqual(openedEvent.payload.options, [
            { decision: "accept", label: "Allow once" },
            { decision: "decline", label: "Decline" },
          ]);
        }

        const invalid = yield* adapter
          .respondToRequest(threadId, ApprovalRequestId.make("interrupt-gated"), "acceptForSession")
          .pipe(Effect.flip);
        assert.include(invalid.message, "is not allowed");
        yield* adapter.respondToRequest(
          threadId,
          ApprovalRequestId.make("interrupt-gated"),
          "accept",
        );
        yield* Deferred.await(completed);
        yield* Fiber.interrupt(eventFiber);

        assert.deepStrictEqual(
          (runBodies[1] as { command?: { resume?: Record<string, unknown> } }).command?.resume,
          { "interrupt-gated": { decisions: [{ type: "approve" }] } },
        );
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("splits multi-action approvals and resumes with decisions in action order", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const runBodies: Array<unknown> = [];
        let runRequest = 0;
        const client = HttpClient.make((request) => {
          if (request.url.endsWith("/threads")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-multi-hitl" })),
            );
          }
          runBodies.push(requestJson(request));
          runRequest += 1;
          return Effect.succeed(
            sseResponse(
              request,
              runRequest === 1
                ? 'event: updates\ndata: {"__interrupt__":[{"id":"interrupt-multi","value":{"action_requests":[{"name":"execute","args":{"command":"git status"}},{"name":"delete","args":{"path":"tmp.txt"}}],"review_configs":[{"action_name":"execute","allowed_decisions":["approve","reject"]},{"action_name":"delete","allowed_decisions":["reject"]}]}}]}\n\n'
                : "event: values\ndata: {}\n\n",
            ),
          );
        });
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test" }),
          instanceId,
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        const openedEvents: ProviderRuntimeEvent[] = [];
        const bothOpened = yield* Deferred.make<void>();
        const completed = yield* Deferred.make<void>();
        const eventFiber = yield* Stream.runForEach(adapter.streamEvents, (event) => {
          if (event.type === "request.opened") {
            openedEvents.push(event);
            return openedEvents.length === 2
              ? Deferred.succeed(bothOpened, undefined)
              : Effect.void;
          }
          return event.type === "turn.completed"
            ? Deferred.succeed(completed, undefined)
            : Effect.void;
        }).pipe(Effect.forkChild);
        yield* Effect.yieldNow;

        yield* adapter.startSession({ threadId, runtimeMode: "approval-required" });
        yield* adapter.sendTurn({ threadId, input: "run both" });
        yield* Deferred.await(bothOpened);

        assert.deepStrictEqual(
          openedEvents.map((event) => String(event.requestId)),
          ["interrupt-multi:0", "interrupt-multi:1"],
        );
        const second = openedEvents[1];
        assert.equal(second?.type, "request.opened");
        if (second?.type === "request.opened") {
          assert.deepStrictEqual(second.payload.options, [
            { decision: "decline", label: "Decline" },
          ]);
        }

        yield* adapter.respondToRequest(
          threadId,
          ApprovalRequestId.make("interrupt-multi:1"),
          "decline",
        );
        assert.equal(runRequest, 1);
        yield* adapter.respondToRequest(
          threadId,
          ApprovalRequestId.make("interrupt-multi:0"),
          "accept",
        );
        yield* Deferred.await(completed);
        yield* Fiber.interrupt(eventFiber);

        assert.deepStrictEqual(
          (runBodies[1] as { command?: { resume?: Record<string, unknown> } }).command?.resume,
          {
            "interrupt-multi": {
              decisions: [
                { type: "approve" },
                { type: "reject", message: "User declined tool execution." },
              ],
            },
          },
        );
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("resumes mixed structured user input and approvals only after both resolve", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const requests: Array<{ url: string; body: unknown }> = [];
        let runRequest = 0;
        const client = HttpClient.make((request) => {
          requests.push({ url: request.url, body: requestJson(request) });
          if (request.url.endsWith("/threads")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-user-input" })),
            );
          }
          runRequest += 1;
          return Effect.succeed(
            sseResponse(
              request,
              runRequest === 1
                ? [
                    'event: updates\ndata: {"__interrupt__":[{"id":"ask-language","value":{"questions":[{"id":"language","header":"Language","question":"Which language should we use?","options":[{"label":"TypeScript","description":"Use TypeScript for the implementation."}],"multiSelect":false}]}},{"id":"approve-command","value":{"action_requests":[{"name":"execute","args":{"command":"git status"}}]}}]}\n',
                    "",
                  ].join("\n")
                : "event: values\ndata: {}\n\n",
            ),
          );
        });
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test" }),
          instanceId,
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        const requested = yield* Deferred.make<ProviderRuntimeEvent>();
        const approvalOpened = yield* Deferred.make<void>();
        const resolved = yield* Deferred.make<ProviderRuntimeEvent>();
        const completed = yield* Deferred.make<void>();
        const eventFiber = yield* Stream.runForEach(adapter.streamEvents, (event) =>
          Effect.all(
            [
              event.type === "user-input.requested"
                ? Deferred.succeed(requested, event)
                : Effect.void,
              event.type === "request.opened"
                ? Deferred.succeed(approvalOpened, undefined)
                : Effect.void,
              event.type === "user-input.resolved"
                ? Deferred.succeed(resolved, event)
                : Effect.void,
              event.type === "turn.completed"
                ? Deferred.succeed(completed, undefined)
                : Effect.void,
            ],
            { discard: true },
          ),
        ).pipe(Effect.forkChild);
        yield* Effect.yieldNow;

        yield* adapter.startSession({ threadId, runtimeMode: "approval-required" });
        yield* adapter.sendTurn({ threadId, input: "choose and inspect" });
        const requestedEvent = yield* Deferred.await(requested);
        yield* Deferred.await(approvalOpened);
        assert.equal(requestedEvent.type, "user-input.requested");
        if (requestedEvent.type === "user-input.requested") {
          assert.equal(requestedEvent.requestId, "ask-language");
          assert.deepStrictEqual(requestedEvent.payload.questions, [
            {
              id: "language",
              header: "Language",
              question: "Which language should we use?",
              options: [
                {
                  label: "TypeScript",
                  description: "Use TypeScript for the implementation.",
                },
              ],
              multiSelect: false,
            },
          ]);
        }

        yield* adapter.respondToUserInput(threadId, ApprovalRequestId.make("ask-language"), {
          language: "TypeScript",
        });
        const resolvedEvent = yield* Deferred.await(resolved);
        assert.equal(resolvedEvent.type, "user-input.resolved");
        if (resolvedEvent.type === "user-input.resolved") {
          assert.deepStrictEqual(resolvedEvent.payload.answers, { language: "TypeScript" });
        }
        assert.equal(runRequest, 1);

        yield* adapter.respondToRequest(
          threadId,
          ApprovalRequestId.make("approve-command"),
          "accept",
        );
        yield* Deferred.await(completed);
        yield* Fiber.interrupt(eventFiber);

        const resumeBody = requests.findLast((entry) => entry.url.endsWith("/runs/stream"))
          ?.body as {
          command?: { resume?: Record<string, unknown> };
        };
        assert.deepStrictEqual(resumeBody.command?.resume, {
          "ask-language": { language: "TypeScript" },
          "approve-command": { decisions: [{ type: "approve" }] },
        });
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("fails closed when LangGraph returns malformed structured user input", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const client = HttpClient.make((request) =>
          Effect.succeed(
            request.url.endsWith("/threads")
              ? HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-bad-input" }))
              : sseResponse(
                  request,
                  'event: updates\ndata: {"__interrupt__":[{"id":"bad-input","value":{"questions":[{"header":"Missing question","options":[]}]}}]}\n\n',
                ),
          ),
        );
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test" }),
          instanceId,
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        const completed = yield* Deferred.make<ProviderRuntimeEvent>();
        const eventFiber = yield* Stream.runForEach(adapter.streamEvents, (event) =>
          event.type === "turn.completed" ? Deferred.succeed(completed, event) : Effect.void,
        ).pipe(Effect.forkChild);

        yield* adapter.startSession({ threadId, runtimeMode: "full-access" });
        yield* adapter.sendTurn({ threadId, input: "ask" });
        const completedEvent = yield* Deferred.await(completed);
        yield* Fiber.interrupt(eventFiber);
        assert.equal(completedEvent.type, "turn.completed");
        if (completedEvent.type === "turn.completed") {
          assert.equal(completedEvent.payload.state, "failed");
          assert.equal(
            completedEvent.payload.errorMessage,
            "The Open SWE run returned a malformed interrupt request.",
          );
        }
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("fails closed when LangGraph returns a malformed interrupt", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const client = HttpClient.make((request) =>
          Effect.succeed(
            request.url.endsWith("/threads")
              ? HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-bad-hitl" }))
              : sseResponse(
                  request,
                  'event: updates\ndata: {"__interrupt__":[{"id":"bad","value":{"action_requests":[{"name":"execute","args":{"command":"git status"}}],"review_configs":[{"action_name":"delete","allowed_decisions":["approve"]}]}}]}\n\n',
                ),
          ),
        );
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test" }),
          instanceId,
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        const completed = yield* Deferred.make<ProviderRuntimeEvent>();
        let opened = false;
        const eventFiber = yield* Stream.runForEach(adapter.streamEvents, (event) => {
          if (event.type === "request.opened") opened = true;
          return event.type === "turn.completed" ? Deferred.succeed(completed, event) : Effect.void;
        }).pipe(Effect.forkChild);
        yield* Effect.yieldNow;

        yield* adapter.startSession({ threadId, runtimeMode: "approval-required" });
        yield* adapter.sendTurn({ threadId, input: "unsafe" });
        const event = yield* Deferred.await(completed);
        yield* Fiber.interrupt(eventFiber);

        assert.isFalse(opened);
        assert.equal(event.type, "turn.completed");
        if (event.type === "turn.completed") {
          assert.equal(event.payload.state, "failed");
          assert.include(event.payload.errorMessage ?? "", "malformed interrupt request");
        }
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("rolls back by forking the checkpoint at the retained turn boundary", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const langGraphThreadId = "lg/history?private=1";
        const requests: Array<{ url: string; body: unknown }> = [];
        const retainedMessages = [
          { type: "human", id: "human-1", content: "first" },
          { type: "ai", id: "ai-1", content: "done" },
        ];
        const client = HttpClient.make((request) => {
          requests.push({ url: request.url, body: requestJson(request) });
          if (request.url.endsWith("/threads")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ thread_id: langGraphThreadId })),
            );
          }
          if (request.url.endsWith("/history")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(
                request,
                Response.json([
                  {
                    checkpoint: {
                      thread_id: langGraphThreadId,
                      checkpoint_ns: "",
                      checkpoint_id: "checkpoint-2",
                    },
                    values: {
                      messages: [
                        ...retainedMessages,
                        { type: "human", id: "human-2", content: "second" },
                        { type: "ai", id: "ai-2", content: "done again" },
                      ],
                    },
                  },
                  {
                    checkpoint: {
                      thread_id: langGraphThreadId,
                      checkpoint_ns: "",
                      checkpoint_id: "checkpoint-1",
                    },
                    values: { messages: retainedMessages },
                  },
                ]),
              ),
            );
          }
          if (request.url.endsWith("/state") && request.method === "POST") {
            return Effect.succeed(
              HttpClientResponse.fromWeb(
                request,
                Response.json({ checkpoint: { checkpoint_id: "checkpoint-fork" } }),
              ),
            );
          }
          return Effect.succeed(
            HttpClientResponse.fromWeb(
              request,
              Response.json({ values: { messages: retainedMessages } }),
            ),
          );
        });
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test" }),
          instanceId,
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        yield* adapter.startSession({ threadId, runtimeMode: "full-access" });

        const snapshot = yield* adapter.rollbackThread(threadId, 1);

        assert.equal(snapshot.turns.length, 1);
        assert.equal(snapshot.turns[0]?.id, "human-1");
        assert.isTrue(
          requests.some((request) =>
            request.url.includes("/threads/lg%2Fhistory%3Fprivate%3D1/history"),
          ),
        );
        const stateUpdate = requests.find(
          (request) => request.url.endsWith("/state") && request.body !== undefined,
        );
        assert.deepStrictEqual(stateUpdate?.body, {
          values: null,
          checkpoint: {
            thread_id: langGraphThreadId,
            checkpoint_ns: "",
            checkpoint_id: "checkpoint-1",
          },
        });
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("validates rollback turn counts before reading LangGraph history", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const requestUrls: string[] = [];
        const client = HttpClient.make((request) => {
          requestUrls.push(request.url);
          return Effect.succeed(
            HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-validation" })),
          );
        });
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test" }),
          instanceId,
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        yield* adapter.startSession({ threadId, runtimeMode: "full-access" });

        const error = yield* Effect.flip(adapter.rollbackThread(threadId, 0));

        assert.equal(error._tag, "ProviderAdapterValidationError");
        assert.isFalse(requestUrls.some((url) => url.endsWith("/history")));
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("rolls back the first turn through its pre-input parent checkpoint", () =>
    Effect.scoped(
      Effect.gen(function* () {
        let stateBody: unknown;
        const client = HttpClient.make((request) => {
          if (request.url.endsWith("/threads")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-first-turn" })),
            );
          }
          if (request.url.endsWith("/history")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(
                request,
                Response.json([
                  {
                    checkpoint: {
                      thread_id: "lg-first-turn",
                      checkpoint_ns: "",
                      checkpoint_id: "checkpoint-after-input",
                    },
                    parent_checkpoint: {
                      thread_id: "lg-first-turn",
                      checkpoint_ns: "",
                      checkpoint_id: "checkpoint-before-input",
                    },
                    values: { messages: [{ type: "human", content: "first" }] },
                  },
                ]),
              ),
            );
          }
          if (request.url.endsWith("/state") && request.method === "POST") {
            stateBody = requestJson(request);
            return Effect.succeed(HttpClientResponse.fromWeb(request, Response.json({})));
          }
          return Effect.succeed(
            HttpClientResponse.fromWeb(request, Response.json({ values: { messages: [] } })),
          );
        });
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test" }),
          instanceId,
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        yield* adapter.startSession({ threadId, runtimeMode: "full-access" });

        const snapshot = yield* adapter.rollbackThread(threadId, 1);

        assert.lengthOf(snapshot.turns, 0);
        assert.deepStrictEqual(stateBody, {
          values: null,
          checkpoint: {
            thread_id: "lg-first-turn",
            checkpoint_ns: "",
            checkpoint_id: "checkpoint-before-input",
          },
        });
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("fails closed when history checkpoints do not belong to the resumed thread", () =>
    Effect.scoped(
      Effect.gen(function* () {
        let stateUpdates = 0;
        const client = HttpClient.make((request) => {
          if (request.url.endsWith("/threads")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ thread_id: "lg-owned" })),
            );
          }
          if (request.url.endsWith("/history")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(
                request,
                Response.json([
                  {
                    checkpoint: {
                      thread_id: "lg-attacker",
                      checkpoint_ns: "",
                      checkpoint_id: "checkpoint-1",
                    },
                    values: { messages: [] },
                  },
                ]),
              ),
            );
          }
          stateUpdates += 1;
          return Effect.succeed(HttpClientResponse.fromWeb(request, Response.json({})));
        });
        const adapter = yield* makeLangGraphAdapter(
          decodeSettings({ serverUrl: "https://example.test" }),
          instanceId,
        ).pipe(Effect.provideService(HttpClient.HttpClient, client));
        yield* adapter.startSession({ threadId, runtimeMode: "full-access" });

        const error = yield* Effect.flip(adapter.rollbackThread(threadId, 1));

        assert.equal(error._tag, "ProviderAdapterRequestError");
        assert.include(error.message, "malformed history");
        assert.equal(stateUpdates, 0);
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );

  it.effect("adds the trusted cwd to the local Open SWE allowlist once", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const fs = yield* FileSystem.FileSystem;
        const root = yield* fs.makeTempDirectoryScoped({ prefix: "open-swe-adapter-" });
        const project = `${root}/project`;
        const allowlist = `${root}/projects.json`;
        yield* fs.makeDirectory(project);
        yield* fs.writeFileString(allowlist, encodeJson([{ cwd: `${root}/existing` }]));
        const previous = process.env.OPEN_SWE_LOCAL_PROJECTS_FILE;
        process.env.OPEN_SWE_LOCAL_PROJECTS_FILE = allowlist;
        yield* Effect.gen(function* () {
          const client = HttpClient.make((request) =>
            Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ thread_id: "local-thread" })),
            ),
          );
          const adapter = yield* makeLangGraphAdapter(
            decodeSettings({ serverUrl: "http://127.0.0.1:2024" }),
            instanceId,
          ).pipe(Effect.provideService(HttpClient.HttpClient, client));
          yield* adapter.startSession({ threadId, cwd: project, runtimeMode: "full-access" });
          const secondThread = ThreadId.make("thread-open-swe-test-2");
          yield* adapter.startSession({
            threadId: secondThread,
            cwd: project,
            runtimeMode: "full-access",
          });
          const entries = decodeJson(yield* fs.readFileString(allowlist)) as Array<unknown>;
          const canonicalProject = yield* fs.realPath(project);
          assert.deepStrictEqual(entries[0], { cwd: `${root}/existing` });
          assert.equal(entries.filter((entry) => entry === canonicalProject).length, 1);
        }).pipe(
          Effect.ensuring(
            Effect.sync(() => {
              if (previous === undefined) delete process.env.OPEN_SWE_LOCAL_PROJECTS_FILE;
              else process.env.OPEN_SWE_LOCAL_PROJECTS_FILE = previous;
            }),
          ),
        );
      }).pipe(Effect.provide(NodeServices.layer)),
    ),
  );
});
