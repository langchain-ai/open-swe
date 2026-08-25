import * as Option from "effect/Option";

export type JoinPath = (first: string, ...segments: string[]) => string;

function normalizeConfiguredBaseDir(opensweHome: Option.Option<string>): Option.Option<string> {
  if (Option.isNone(opensweHome)) {
    return Option.none();
  }
  const trimmed = opensweHome.value.trim();
  return trimmed.length > 0 ? Option.some(trimmed) : Option.none();
}

export function resolveDesktopBaseDir(input: {
  readonly homeDirectory: string;
  readonly joinPath: JoinPath;
  readonly opensweHome: Option.Option<string>;
}): string {
  return Option.getOrElse(normalizeConfiguredBaseDir(input.opensweHome), () =>
    input.joinPath(input.homeDirectory, ".open-swe"),
  );
}

export function resolveDesktopStateDir(input: {
  readonly baseDir: string;
  readonly isDevelopment: boolean;
  readonly joinPath: JoinPath;
  readonly opensweHome: Option.Option<string>;
}): string {
  const useDevSubdir =
    input.isDevelopment && Option.isNone(normalizeConfiguredBaseDir(input.opensweHome));
  return input.joinPath(input.baseDir, useDevSubdir ? "dev" : "userdata");
}
