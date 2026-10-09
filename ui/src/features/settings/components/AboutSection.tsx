import { useEffect, useState } from "react"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { CopyDiagnosticsButton } from "@/components/CopyDiagnosticsButton"
import { buildEnvironmentDiagnostics } from "@/lib/environment-diagnostics"
import {
  describeApiBase,
  normalizeBuildInfo,
  type BuildInfo,
  type SessionUser,
} from "@/lib/api"
import { useIsHydrated } from "@/lib/hydration"

export function AboutSection({ user }: { user: SessionUser }) {
  const [version, setVersion] = useState<string>()
  const buildInfo = normalizeBuildInfo(user.build_info)
  const apiBase = describeApiBase(user.api_base_url)

  useEffect(() => {
    void window.openSweDesktop?.getVersion().then(setVersion)
  }, [])

  return (
    <SettingsSection id="about" title="About">
      {version ? (
        <SettingsRow
          label="Open SWE Desktop"
          control={
            <span className="text-xs text-secondary">Version {version}</span>
          }
        />
      ) : null}
      <div className="space-y-2 p-4 text-xs break-words text-secondary">
        <p>
          API: {apiBase.origin ?? "same origin"} {apiBase.path}
        </p>
        <p>
          OPENSWE_ENV: <IdentityValue value={buildInfo?.backend.environment} />
        </p>
        <BuildIdentityDetails buildInfo={buildInfo} />
        <CopyDiagnosticsButton
          getDiagnostics={() => buildEnvironmentDiagnostics(user, version)}
        />
      </div>
    </SettingsSection>
  )
}

function IdentityValue({ value }: { value: string | null | undefined }) {
  return value ? (
    <code className="select-all">{value}</code>
  ) : (
    <span>Unavailable</span>
  )
}

function CommitValue({ value }: { value: string | null }) {
  if (!value) return <IdentityValue value={value} />
  return (
    <a
      href={`https://github.com/langchain-ai/open-swe/commit/${encodeURIComponent(value)}`}
      target="_blank"
      rel="noopener noreferrer"
      className="underline underline-offset-2 hover:text-primary"
    >
      <IdentityValue value={value} />
    </a>
  )
}

function BuildIdentityDetails({ buildInfo }: { buildInfo: BuildInfo | null }) {
  const hydrated = useIsHydrated()

  return (
    <>
      {buildInfo ? (
        <>
          <p>
            Backend: revision{" "}
            <IdentityValue value={buildInfo.backend.revision_id} />
            {" · "}commit <CommitValue value={buildInfo.backend.commit} />
            {" · "}built{" "}
            {buildInfo.backend.built_at ? (
              <time dateTime={buildInfo.backend.built_at}>
                {new Date(buildInfo.backend.built_at).toLocaleString()}
              </time>
            ) : (
              "Unavailable"
            )}
            {" · "}package{" "}
            <IdentityValue value={buildInfo.backend.package_version} />
          </p>
          <p>
            Web bundle:{" "}
            {buildInfo.web.served ? (
              <>
                commit <CommitValue value={buildInfo.web.commit} />
                {" · "}built{" "}
                {buildInfo.web.built_at ? (
                  <time dateTime={buildInfo.web.built_at}>
                    {new Date(buildInfo.web.built_at).toLocaleString()}
                  </time>
                ) : (
                  "Unavailable"
                )}
              </>
            ) : (
              "not served by this backend"
            )}
          </p>
        </>
      ) : (
        <p>
          Build identifiers: Unavailable (the connected backend does not report
          them).
        </p>
      )}
      {(() => {
        const bundle = hydrated ? window.__OPEN_SWE_BUNDLE__ : undefined
        if (!bundle) return null
        const comparable =
          buildInfo?.web.served &&
          bundle.commit != null &&
          buildInfo?.web.commit != null
        const differs = comparable && bundle.commit !== buildInfo?.web.commit
        return (
          <p>
            This browser is running: commit{" "}
            <CommitValue value={bundle.commit} />
            {" · "}built{" "}
            <time dateTime={bundle.built_at}>
              {new Date(bundle.built_at).toLocaleString()}
            </time>
            {differs ? (
              <span className="text-warning-secondary">
                {" "}
                — different from the bundle the backend reports serving.
              </span>
            ) : buildInfo?.web.served && !comparable ? (
              <span className="text-secondary">
                {" "}
                — comparison with the backend-served bundle unavailable.
              </span>
            ) : null}
          </p>
        )
      })()}
    </>
  )
}
