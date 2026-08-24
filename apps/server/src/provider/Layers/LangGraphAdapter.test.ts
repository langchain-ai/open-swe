import * as NodeServices from "@effect/platform-node/NodeServices";
import {
  LangGraphSettings,
  ProviderDriverKind,
  ProviderInstanceId,
  type ProviderRuntimeEvent,
  ThreadId,
} from "@t3tools/contracts";
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
const instanceId = ProviderInstanceId.make("open-swe-test");
const threadId = ThreadId.make("thread-open-swe-test");
const encoder = new TextEncoder();

function requestJson(request: { readonly body: unknown }): unknown {
  const body = request.body as { readonly _tag?: string; readonly body?: Uint8Array };
  return body._tag === "Uint8Array" && body.body !== undefined
    ? JSON.parse(new TextDecoder().decode(body.body))
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
        const requests: Array<{ url: string; body: unknown }> = [];
        const client = HttpClient.make((request) => {
          requests.push({ url: request.url, body: requestJson(request) });
          if (request.url.endsWith("/threads")) {
            return Effect.succeed(
              HttpClientResponse.fromWeb(request, Response.json({ thread_id: String(threadId) })),
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
          config?: { configurable?: Record<string, unknown> };
        };
        assert.deepInclude(runBody.config?.configurable, {
          source: "desktop",
          local_project_path: "/trusted/project",
          agent_model_id: "openai:gpt-5.6-terra",
        });
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

  it.effect("adds the trusted cwd to the local Open SWE allowlist once", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const fs = yield* FileSystem.FileSystem;
        const root = yield* fs.makeTempDirectoryScoped({ prefix: "open-swe-adapter-" });
        const project = `${root}/project`;
        const allowlist = `${root}/projects.json`;
        yield* fs.makeDirectory(project);
        yield* fs.writeFileString(allowlist, JSON.stringify([{ cwd: `${root}/existing` }]));
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
          const entries = JSON.parse(yield* fs.readFileString(allowlist)) as Array<unknown>;
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
