import { defineSandbox } from "managed-deepagents";

// Declare the managed LangSmith execution environment for this agent. MDA owns
// the run-scoped name, reuse across turns, and lifecycle. Delete this directory
// to run without a sandbox.
//
// To provision a recipe snapshot, add `sandbox/setup.sh`. `mda deploy` and
// `mda dev` bake it once; new threads clone it without re-running the script.
export const sandbox = defineSandbox({
  // Reclaim idle sandboxes after 10 minutes.
  idleTtlSeconds: 600,
  // Default per-command timeout, in seconds.
  defaultTimeout: 600,
});
