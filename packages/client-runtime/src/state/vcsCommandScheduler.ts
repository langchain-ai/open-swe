import type { EnvironmentId } from "@openswe/contracts";

import { createAtomCommandScheduler, type AtomCommandConcurrency } from "./runtime.ts";

export const vcsCommandScheduler = createAtomCommandScheduler();

export const vcsCommandConcurrency: AtomCommandConcurrency<{
  readonly environmentId: EnvironmentId;
  readonly input: { readonly cwd: string };
}> = {
  mode: "serial",
  key: ({ environmentId, input }) => JSON.stringify([environmentId, input.cwd]),
};
