import { Link } from "@tanstack/react-router"
import { useMemo, type ReactNode } from "react"
import {
  DesignSystemProvider,
  type LinkSlotProps,
} from "@langchain/gtm-platform-design-system/host"
import { TooltipProvider } from "@langchain/gtm-platform-design-system/ui/tooltip"

import { useHrefLinkOptions } from "@/lib/appLocation"
import { useTheme } from "@/lib/theme"

const EXTERNAL_HREF = /^https?:\/\//i

/** The design system's `link` slot: router links in-app, new tabs off-site. */
function RouterLink({ href, children, ...rest }: LinkSlotProps) {
  const linkOptions = useHrefLinkOptions()
  if (EXTERNAL_HREF.test(href)) {
    return (
      <a href={href} target="_blank" {...rest} rel="noopener noreferrer">
        {children}
      </a>
    )
  }
  return (
    <Link {...linkOptions(href)} {...rest}>
      {children}
    </Link>
  )
}

/** Mounts the design system once: router links, the app's theme, one tooltip clock. */
export function DesignSystemHost({ children }: { children: ReactNode }) {
  const { theme, setTheme } = useTheme()
  const themeSlot = useMemo(() => ({ theme, setTheme }), [theme, setTheme])
  return (
    <DesignSystemProvider link={RouterLink} theme={themeSlot}>
      <TooltipProvider>{children}</TooltipProvider>
    </DesignSystemProvider>
  )
}
