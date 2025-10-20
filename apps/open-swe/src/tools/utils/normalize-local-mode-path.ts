import path from "node:path";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import {
  getLocalWorkingDirectory,
  isLocalMode,
} from "@openswe/shared/open-swe/local-mode";

interface ResolvedLocalPath {
  absolutePath: string;
  relativePath: string;
}

function removePrefix(input: string, prefix: string): string {
  if (input === prefix) {
    return "";
  }

  if (input.startsWith(prefix)) {
    return input.slice(prefix.length);
  }

  return input;
}

export function resolveLocalModePath(
  config: GraphConfig,
  requestedPath: string,
): ResolvedLocalPath {
  if (!isLocalMode(config)) {
    throw new Error(
      "resolveLocalModePath can only be used when running in local mode.",
    );
  }

  const workspaceRoot = path.resolve(getLocalWorkingDirectory());
  const normalizedRoot = workspaceRoot.replace(/\\/g, "/");
  let sanitized = (requestedPath ?? "").replace(/\\/g, "/").trim();

  if (sanitized === "") {
    return { absolutePath: workspaceRoot, relativePath: "." };
  }

  if (sanitized.startsWith("./")) {
    sanitized = sanitized.slice(2);
  }

  const rootPrefixes = [normalizedRoot, `${normalizedRoot}/`];
  for (const prefix of rootPrefixes) {
    sanitized = removePrefix(sanitized, prefix);
  }

  sanitized = sanitized.replace(/^\/+/, "");

  const sandboxPrefixes = [
    "project/",
    "project",
    "/project/",
    "/project",
    "sandbox/project/",
    "sandbox/project",
    "/sandbox/project/",
    "/sandbox/project",
    "workspace/project/",
    "workspace/project",
    "/workspace/project/",
    "/workspace/project",
  ];

  for (const prefix of sandboxPrefixes) {
    const updated = removePrefix(sanitized, prefix);
    if (updated !== sanitized) {
      sanitized = updated;
      break;
    }
  }

  sanitized = sanitized.replace(/^\/+/, "");

  const absolutePath = path.resolve(workspaceRoot, sanitized);
  const rootWithSeparator = workspaceRoot.endsWith(path.sep)
    ? workspaceRoot
    : `${workspaceRoot}${path.sep}`;

  if (
    absolutePath !== workspaceRoot &&
    !absolutePath.startsWith(rootWithSeparator)
  ) {
    throw new Error(
      `Path '${requestedPath}' resolves outside of the local workspace root`,
    );
  }

  const relativePath = path.relative(workspaceRoot, absolutePath) || ".";

  return { absolutePath, relativePath };
}
