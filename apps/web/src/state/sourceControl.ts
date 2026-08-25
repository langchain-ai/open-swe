import { createSourceControlEnvironmentAtoms } from "@openswe/client-runtime/state/source-control";

import { connectionAtomRuntime } from "../connection/runtime";

export const sourceControlEnvironment = createSourceControlEnvironmentAtoms(connectionAtomRuntime);
