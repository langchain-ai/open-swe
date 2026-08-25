import { createAuthEnvironmentAtoms } from "@openswe/client-runtime/state/auth";

import { connectionAtomRuntime } from "../connection/runtime";

export const authEnvironment = createAuthEnvironmentAtoms(connectionAtomRuntime);
