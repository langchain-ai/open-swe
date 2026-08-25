import { LangGraphSettings, ProviderInstanceId, TextGenerationError } from "@openswe/contracts";
import { assert, describe, it } from "@effect/vitest";
import * as Effect from "effect/Effect";
import * as Schema from "effect/Schema";
import { HttpClient, HttpClientResponse } from "effect/unstable/http";

import { makeLangGraphTextGeneration } from "./LangGraphTextGeneration.ts";

const decodeSettings = Schema.decodeSync(LangGraphSettings);
const instanceId = ProviderInstanceId.make("langgraph-test");

function requestJson(request: { readonly body: unknown }): unknown {
  const body = request.body as { readonly _tag?: string; readonly body?: Uint8Array };
  return body._tag === "Uint8Array" && body.body !== undefined
    ? JSON.parse(new TextDecoder().decode(body.body))
    : undefined;
}

describe("LangGraphTextGeneration", () => {
  it.effect("generates and sanitizes every text shape through isolated guarded runs", () =>
    Effect.gen(function* () {
      const outputs = [
        '{"subject":"Add useful support.","body":"  Details  ","branch":"Feature/Useful Support"}',
        '{"title":"Improve provider support.","body":"  ## Summary\\n- Better  "}',
        '{"branch":"Better Names!!!"}',
        '{"title":"  Improve Provider Titles.  "}',
      ];
      const requests: Array<{
        method: string;
        url: string;
        headers: Readonly<Record<string, string>>;
        body: unknown;
      }> = [];
      let threadSequence = 0;
      const client = HttpClient.make((request) => {
        requests.push({
          method: request.method,
          url: request.url,
          headers: request.headers,
          body: requestJson(request),
        });
        if (request.method === "DELETE") {
          return Effect.succeed(
            HttpClientResponse.fromWeb(request, new Response(null, { status: 204 })),
          );
        }
        if (request.url.endsWith("/threads")) {
          threadSequence += 1;
          return Effect.succeed(
            HttpClientResponse.fromWeb(
              request,
              Response.json({ thread_id: `text/thread-${String(threadSequence)}` }),
            ),
          );
        }
        const output = outputs.shift();
        return Effect.succeed(
          HttpClientResponse.fromWeb(
            request,
            Response.json({
              messages: [
                { type: "human", content: "prompt" },
                { type: "ai", content: `\n\`\`\`json\n${output}\n\`\`\`` },
              ],
            }),
          ),
        );
      });
      const textGeneration = yield* makeLangGraphTextGeneration(
        decodeSettings({
          serverUrl: "https://langgraph.example.test/",
          graphId: "agent",
        }),
        { OPEN_SWE_LOCAL_AUTH_TOKEN: "text-generation-placeholder" },
      ).pipe(Effect.provideService(HttpClient.HttpClient, client));
      const modelSelection = {
        instanceId,
        model: "openai:gpt-5.6-sol",
        options: [{ id: "effort", value: "high" }] as const,
      };

      const commit = yield* textGeneration.generateCommitMessage({
        cwd: "/trusted/project",
        branch: "feature/current",
        stagedSummary: "M src/file.ts",
        stagedPatch: "diff --git a/src/file.ts b/src/file.ts",
        includeBranch: true,
        modelSelection,
      });
      const pr = yield* textGeneration.generatePrContent({
        cwd: "/trusted/project",
        baseBranch: "main",
        headBranch: "feature/current",
        commitSummary: "Add support",
        diffSummary: "1 file changed",
        diffPatch: "diff",
        modelSelection,
      });
      const branch = yield* textGeneration.generateBranchName({
        cwd: "/trusted/project",
        message: "Please improve provider names",
        modelSelection,
      });
      const title = yield* textGeneration.generateThreadTitle({
        cwd: "/trusted/project",
        message: "Please improve provider titles",
        modelSelection,
      });

      assert.deepStrictEqual(commit, {
        subject: "Add useful support",
        body: "Details",
        branch: "feature/useful-support",
      });
      assert.deepStrictEqual(pr, {
        title: "Improve provider support.",
        body: "## Summary\n- Better",
      });
      assert.deepStrictEqual(branch, { branch: "better-names" });
      assert.deepStrictEqual(title, { title: "Improve Provider Titles." });
      assert.lengthOf(
        requests.filter((request) => request.url.endsWith("/threads")),
        4,
      );
      assert.lengthOf(
        requests.filter((request) => request.method === "DELETE"),
        4,
      );
      assert.isTrue(
        requests
          .filter((request) => request.method === "DELETE")
          .every((request) => request.url.includes("text%2Fthread-")),
      );

      const run = requests.find((request) => request.url.endsWith("/runs/wait"));
      assert.equal(run?.headers["x-api-key"], "text-generation-placeholder");
      assert.equal(run?.headers["authorization"], "Bearer text-generation-placeholder");
      assert.deepInclude(run?.body as object, {
        assistant_id: "agent",
        config: {
          configurable: {
            source: "desktop",
            local_project_path: "/trusted/project",
            agent_model_id: "openai:gpt-5.6-sol",
            agent_effort: "high",
            plan_mode: false,
            runtime_mode: "approval-required",
          },
        },
      });
    }),
  );

  it.effect("fails with a typed error for invalid structured output and still cleans up", () =>
    Effect.gen(function* () {
      let deleted = false;
      const client = HttpClient.make((request) => {
        if (request.method === "DELETE") {
          deleted = true;
          return Effect.succeed(
            HttpClientResponse.fromWeb(request, new Response(null, { status: 204 })),
          );
        }
        return Effect.succeed(
          HttpClientResponse.fromWeb(
            request,
            request.url.endsWith("/threads")
              ? Response.json({ thread_id: "temporary" })
              : Response.json({ messages: [{ role: "assistant", content: "not json" }] }),
          ),
        );
      });
      const textGeneration = yield* makeLangGraphTextGeneration(
        decodeSettings({ serverUrl: "https://langgraph.example.test" }),
      ).pipe(Effect.provideService(HttpClient.HttpClient, client));

      const error = yield* textGeneration
        .generateBranchName({
          cwd: "/trusted/project",
          message: "Name this work",
          modelSelection: { instanceId, model: "openai:gpt-5.6-terra" },
        })
        .pipe(Effect.flip);

      assert.instanceOf(error, TextGenerationError);
      assert.equal(error.operation, "generateBranchName");
      assert.include(error.detail, "invalid structured output");
      assert.isTrue(deleted);
    }),
  );
});
