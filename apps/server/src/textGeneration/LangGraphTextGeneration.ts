/**
 * LangGraphTextGeneration — commit/PR/branch/title generation for the Open
 * SWE provider.
 *
 * STATUS: scaffold, matching LangGraphAdapter. Open SWE's agent writes its
 * own commits and PR bodies inside the run, so these host-side helpers have
 * no natural mapping yet; the likely implementation is a call to the `chat`
 * graph declared in langgraph.json. Until then every method fails with a
 * typed error so callers fall back rather than silently committing an empty
 * message.
 *
 * @module textGeneration/LangGraphTextGeneration
 */
import { TextGenerationError } from "@t3tools/contracts";
import * as Effect from "effect/Effect";

import type { TextGeneration } from "./TextGeneration.ts";

const notWired = (operation: string) =>
  Effect.fail(
    new TextGenerationError({
      operation,
      detail: "Text generation is not wired for the Open SWE provider yet.",
    }),
  );

export function makeLangGraphTextGeneration(): Effect.Effect<TextGeneration["Service"]> {
  return Effect.succeed({
    generateCommitMessage: () => notWired("generateCommitMessage"),
    generatePrContent: () => notWired("generatePrContent"),
    generateBranchName: () => notWired("generateBranchName"),
    generateThreadTitle: () => notWired("generateThreadTitle"),
  });
}
