import type { EnvironmentId } from "@openswe/contracts";

export interface VcsStatusTarget {
  readonly environmentId: EnvironmentId | null;
  readonly cwd: string | null;
}
