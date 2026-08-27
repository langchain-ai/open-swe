import { useMemo } from "react"

import {
  ARTIFACT_ALLOW,
  ARTIFACT_SANDBOX,
  withArtifactShell,
} from "@/features/agents/lib/artifactShell"
import { SandboxedHtmlFrame } from "@/features/agents/components/SandboxedHtmlFrame"
import { useResolvedTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"

export function PlanArtifactFrame({
  html,
  title = "Plan artifact",
  className,
}: {
  html: string
  title?: string
  className?: string
}) {
  const theme = useResolvedTheme()
  const srcDoc = useMemo(() => withArtifactShell(html, theme), [html, theme])

  return (
    <SandboxedHtmlFrame
      testId="plan-artifact-frame"
      title={title}
      html={srcDoc}
      sandbox={ARTIFACT_SANDBOX}
      allow={ARTIFACT_ALLOW}
      className={cn("bg-background", className)}
    />
  )
}
