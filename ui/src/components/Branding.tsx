import type { ComponentProps } from "react"
import { useQuery } from "@tanstack/react-query"
import { dashboardApiBase } from "@/lib/api-base"
import { useIsHydrated } from "@/lib/hydration"
import { expiresInBrowser } from "@/lib/query"
import { cn } from "@/lib/utils"

function useBranding() {
  const hydrated = useIsHydrated()
  const base = dashboardApiBase()
  const { data: environment } = useQuery({
    queryKey: ["branding", base],
    enabled: hydrated,
    ...expiresInBrowser,
    queryFn: async ({ signal }) => {
      const response = await fetch(`${base}/dashboard/api/analytics/config`, {
        cache: "no-store",
        signal: AbortSignal.any([signal, AbortSignal.timeout(5000)]),
      })
      if (!response.ok) {
        throw new Error(`Branding configuration: ${response.status}`)
      }
      const config: unknown = await response.json()
      return typeof config === "object" &&
        config !== null &&
        "environment" in config &&
        (config.environment === "preview" || config.environment === "staging")
        ? config.environment
        : null
    },
  })
  const suffix = environment ? `-${environment}` : ""
  return {
    logo: `${import.meta.env.BASE_URL}bird-mark.png`,
    favicon: `${import.meta.env.BASE_URL}favicon${suffix}.png`,
  }
}

export function LogoMark({
  alt,
  className,
  style,
  ...props
}: Omit<ComponentProps<"span">, "children"> & { alt: string }) {
  const branding = useBranding()
  return (
    <span
      role={alt ? "img" : undefined}
      aria-label={alt || undefined}
      aria-hidden={alt ? undefined : true}
      {...props}
      className={cn(
        "inline-block bg-current text-icon-brand-fill dark:text-icon-brand-background",
        className
      )}
      style={{
        maskImage: `url("${branding.logo}")`,
        maskSize: "contain",
        maskRepeat: "no-repeat",
        maskPosition: "center",
        ...style,
      }}
    />
  )
}

export function Favicon() {
  const branding = useBranding()
  return <link rel="icon" type="image/png" href={branding.favicon} />
}
