import { useEffect, useState } from "react"

import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { Stack } from "@langchain/gtm-platform-design-system/ui/box"
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
    <PageSection id="about" title="About" contained inset="padded">
      <Stack gap="sm" className="text-meta break-words text-ink-subtle">
        {version ? (
          <p>
            <span className="font-medium text-ink">Open SWE Desktop</span> ·
            Version {version}
          </p>
        ) : null}
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
      </Stack>
    </PageSection>
  )
}

function IdentityValue({ value }: { value: string | null | undefined }) {
  return value ? (
    <code className="font-mono text-ink select-all">{value}</code>
  ) : (
    <span>Unavailable</span>
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
            {" · "}commit <IdentityValue value={buildInfo.backend.commit} />
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
            Dashboard bundle:{" "}
            {buildInfo.dashboard.served ? (
              <>
                commit <IdentityValue value={buildInfo.dashboard.commit} />
                {" · "}built{" "}
                {buildInfo.dashboard.built_at ? (
                  <time dateTime={buildInfo.dashboard.built_at}>
                    {new Date(buildInfo.dashboard.built_at).toLocaleString()}
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
          buildInfo?.dashboard.served &&
          bundle.commit != null &&
          buildInfo?.dashboard.commit != null
        const differs =
          comparable && bundle.commit !== buildInfo?.dashboard.commit
        return (
          <p>
            This browser is running: commit{" "}
            <IdentityValue value={bundle.commit} />
            {" · "}built{" "}
            <time dateTime={bundle.built_at}>
              {new Date(bundle.built_at).toLocaleString()}
            </time>
            {differs ? (
              <span className="text-attention">
                {" "}
                — different from the bundle the backend reports serving.
              </span>
            ) : buildInfo?.dashboard.served && !comparable ? (
              <span className="text-ink-subtle">
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
