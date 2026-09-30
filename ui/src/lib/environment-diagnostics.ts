import {
  describeApiBase,
  normalizeBuildInfo,
  type SessionUser,
} from "@/lib/api"

export function buildEnvironmentDiagnostics(
  user: SessionUser,
  desktopVersion: string | undefined
) {
  const build = normalizeBuildInfo(user.build_info)
  const bundle = window.__OPEN_SWE_BUNDLE__
  return {
    report: "open-swe-environment-diagnostics",
    generated_at: new Date().toISOString(),
    api: describeApiBase(user.api_base_url),
    build: build ?? "unavailable_from_backend",
    running_bundle: bundle
      ? { commit: bundle.commit, built_at: bundle.built_at }
      : null,
    desktop_version: desktopVersion ?? null,
  }
}
