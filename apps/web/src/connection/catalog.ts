import { createEnvironmentCatalogAtoms } from "@openswe/client-runtime/state/connections";

import { connectionAtomRuntime } from "./runtime";

export const environmentCatalog = createEnvironmentCatalogAtoms(connectionAtomRuntime);
