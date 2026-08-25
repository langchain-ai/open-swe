import {
  OPEN_SWE_PROJECT_FILE_NAME,
  type EnvironmentId,
  type OpenSWEProjectFile,
  type OpenSWEProjectFileScript,
} from "@openswe/contracts";
import { parseOpenSWEProjectFile } from "@openswe/shared/opensweProjectFile";
import { useMemo } from "react";

import { useProjectFileQuery } from "~/components/files/projectFilesQueryState";

const NO_SCRIPTS: ReadonlyArray<OpenSWEProjectFileScript> = [];

export interface OpenSWEProjectFileState {
  /**
   * - `valid`: openswe.json exists and decoded.
   * - `invalid`: openswe.json exists but fails to decode (the server then ignores
   *   the whole file, including `iconPath` and every script).
   * - `missing`: no readable openswe.json at the workspace root.
   * - `loading`: the file query has not settled yet.
   */
  status: "loading" | "missing" | "invalid" | "valid";
  /** The decoded file when status is `valid`, null otherwise. */
  file: OpenSWEProjectFile | null;
  scripts: ReadonlyArray<OpenSWEProjectFileScript>;
}

/**
 * Decoded state of the project's checked-in `openswe.json`, including whether the
 * file exists but is broken — which the runtime otherwise swallows silently.
 */
export function useOpenSWEProjectFileState(
  environmentId: EnvironmentId,
  cwd: string | null,
): OpenSWEProjectFileState {
  const query = useProjectFileQuery(
    environmentId,
    cwd ?? "",
    OPEN_SWE_PROJECT_FILE_NAME,
    cwd !== null,
  );
  const contents = query.data && !query.data.truncated ? query.data.contents : null;
  const isPending = query.isPending;
  return useMemo(() => {
    if (contents === null) {
      return {
        status: isPending ? "loading" : "missing",
        file: null,
        scripts: NO_SCRIPTS,
      } as const;
    }
    const file = parseOpenSWEProjectFile(contents);
    if (file === null) {
      return { status: "invalid", file: null, scripts: NO_SCRIPTS } as const;
    }
    return { status: "valid", file, scripts: file.scripts ?? NO_SCRIPTS } as const;
  }, [contents, isPending]);
}

/**
 * Scripts declared in the project's checked-in `openswe.json`, offered in the
 * scripts menu for import. Missing, truncated, or invalid files resolve to
 * an empty list.
 */
export function useOpenSWEProjectFileScripts(
  environmentId: EnvironmentId,
  cwd: string | null,
): ReadonlyArray<OpenSWEProjectFileScript> {
  return useOpenSWEProjectFileState(environmentId, cwd).scripts;
}
