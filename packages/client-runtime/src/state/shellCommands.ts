import { WS_METHODS } from "@openswe/contracts";
import { Atom } from "effect/unstable/reactivity";

import { createEnvironmentRpcCommand } from "./runtime.ts";
import type { EnvironmentRegistry } from "../connection/registry.ts";

export function createShellEnvironmentAtoms<R, E>(
  runtime: Atom.AtomRuntime<EnvironmentRegistry | R, E>,
) {
  return {
    openInEditor: createEnvironmentRpcCommand(runtime, {
      label: "environment-data:shell:open-in-editor",
      tag: WS_METHODS.shellOpenInEditor,
    }),
  };
}
