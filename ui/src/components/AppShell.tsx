import { Navigate, useNavigate } from "@tanstack/react-router"
import type { ReactNode } from "react"
import { AppShell as ShellPattern } from "@langchain/gtm-platform-design-system/patterns/app-shell"
import { PageBand } from "@langchain/gtm-platform-design-system/patterns/page-band"
import { PageFrame } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { PageMasthead } from "@langchain/gtm-platform-design-system/patterns/page-masthead"
import { Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"

import type { SessionUser } from "@/lib/api"
import { SettingsNav } from "@/components/SettingsNav"
import { SidebarUserMenu } from "@/components/SidebarUserMenu"
import { AppRailSettingsBrand } from "@/components/rail/AppRailBrand"
import { useRailCollapsed } from "@/components/rail/useRailCollapsed"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"

/**
 * The route's measure. `reading` is one 768px column (forms, settings);
 * `work` is 1280 for a readable list; `wide` drops the cap for grids that
 * scroll inside themselves.
 */
export type AppShellWidth = "reading" | "work" | "wide"

interface AppShellProps {
  user: SessionUser
  title: string
  description?: string
  /** The page's one header control. */
  action?: ReactNode
  /** An object route: the band names the parent and goes back to it. */
  backTo?: { to: string; label: string }
  contentWidth?: AppShellWidth
  children: ReactNode
}

function PageHeader({
  title,
  description,
  action,
}: Pick<AppShellProps, "title" | "description" | "action">) {
  return (
    <Inline gap="lg" justify="between" align="start" wrap>
      <PageMasthead title={title} description={description} />
      {action}
    </Inline>
  )
}

/** The settings and workspace shell: scoped settings rail, one measure, one page header. */
export function AppShell({
  user,
  title,
  description,
  action,
  backTo,
  contentWidth = "reading",
  children,
}: AppShellProps) {
  const rail = useRailCollapsed()
  const navigate = useNavigate()
  const railProps = {
    sidebar: <SettingsNav user={user} collapsed={rail.collapsed} />,
    railHeader: (
      <AppRailSettingsBrand
        collapsed={rail.collapsed}
        onToggleCollapsed={rail.toggle}
      />
    ),
    railFooter: <SidebarUserMenu user={user} collapsed={rail.collapsed} />,
    railCollapsed: rail.collapsed,
  }

  const page =
    contentWidth === "reading" ? (
      <PageFrame
        title={title}
        description={description}
        actions={action}
      >
        {children}
      </PageFrame>
    ) : (
      <Stack gap="xl" className="py-6">
        <PageHeader title={title} description={description} action={action} />
        {children}
      </Stack>
    )

  return (
    <div className="h-svh">
      {backTo ? (
        <ShellPattern
          mode="lineage"
          contentWidth={contentWidth}
          band={
            <PageBand
              variant="lineage"
              lineage={[{ label: backTo.label, href: backTo.to }, { label: title }]}
              backLabel={backTo.label}
              onBack={() => void navigate({ to: backTo.to })}
            />
          }
          {...railProps}
        >
          {page}
        </ShellPattern>
      ) : (
        <ShellPattern mode="flat" contentWidth={contentWidth} {...railProps}>
          {page}
        </ShellPattern>
      )}
    </div>
  )
}

interface SettingsPageProps extends Omit<AppShellProps, "user" | "children"> {
  adminOnly?: boolean
  children: ReactNode | ((user: SessionUser) => ReactNode)
}

/** An AppShell page that waits for the session and requires sign-in (and admin, when asked). */
export function SettingsPage({
  adminOnly,
  children,
  ...props
}: SettingsPageProps) {
  const session = useSession()
  if (session.isLoading) {
    return (
      <main className="flex h-svh bg-desk">
        <div className="w-61.5 shrink-0 bg-sidebar" />
        <div className="flex-1 bg-shell p-6">
          <Skeleton className="mx-auto h-40 w-full max-w-reading" />
        </div>
      </main>
    )
  }
  if (!session.data) return <RequireLogin />
  if (adminOnly && !session.data.is_admin) return <Navigate to="/my-settings" />
  return (
    <AppShell user={session.data} {...props}>
      {typeof children === "function" ? children(session.data) : children}
    </AppShell>
  )
}
