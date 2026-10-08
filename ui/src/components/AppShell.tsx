import { Badge } from "@langchain/macaw-components/Badge"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { ArrowLeftIcon } from "@phosphor-icons/react/dist/ssr/ArrowLeft"
import { CaretRightIcon } from "@phosphor-icons/react/dist/ssr/CaretRight"
import { Link, Navigate } from "@tanstack/react-router"
import type { ReactNode } from "react"

import type { SessionUser } from "@/lib/api"
import { AppSidebar } from "@/components/AppSidebar"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

interface AppShellProps {
  user: SessionUser
  title: string
  action?: ReactNode
  description?: string
  backTo?: { to: string; label: string }
  className?: string
  children: ReactNode
}

export function AppShell({
  user,
  title,
  action,
  description,
  backTo,
  className,
  children,
}: AppShellProps) {
  return (
    <div className="flex h-svh overflow-hidden bg-surface-level-1 text-primary">
      <AppSidebar user={user} />
      <main className="relative flex-1 overflow-y-auto">
        <div
          className={cn(
            "mx-auto max-w-3xl px-4 pt-14 pb-16 sm:px-8 sm:py-12",
            className
          )}
        >
          {backTo && (
            <Link
              to={backTo.to}
              className="mb-4 inline-flex items-center gap-1.5 text-xs text-secondary hover:text-primary"
            >
              <ArrowLeftIcon size={14} weight="regular" />
              {backTo.label}
            </Link>
          )}
          <header className="mb-10 flex flex-wrap items-center justify-between gap-4">
            <div>
              <h1 className="font-heading text-xl font-medium tracking-tight">
                {title}
              </h1>
              {description && (
                <p className="mt-1.5 max-w-2xl text-xs text-secondary">
                  {description}
                </p>
              )}
            </div>
            {action}
          </header>
          <div className="space-y-10">{children}</div>
        </div>
      </main>
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
      <main className="p-6">
        <Skeleton className="h-40 w-full" />
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

interface SettingsSectionProps {
  id?: string
  title: ReactNode
  description?: string
  action?: ReactNode
  children?: ReactNode
}

/** A titled group of rows rendered as a single card. */
export function SettingsSection({
  id,
  title,
  description,
  action,
  children,
}: SettingsSectionProps) {
  return (
    <section id={id} className="space-y-3">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-medium text-primary">{title}</h2>
          {description && (
            <p className="mt-1 max-w-2xl text-xs text-secondary">
              {description}
            </p>
          )}
        </div>
        {action}
      </div>
      {children && (
        <div className="divide-y divide-default overflow-hidden rounded-xl border border-default bg-surface-level-1">
          {children}
        </div>
      )}
    </section>
  )
}

interface SettingsRowProps {
  label: string
  description?: ReactNode
  control: ReactNode
  htmlFor?: string
  comingSoon?: boolean
  /** A short tag after the label, such as where a value comes from. */
  badge?: string
  badgeClassName?: string
}

/** Label + description on the left, a single control on the right. */
export function SettingsRow({
  label,
  description,
  control,
  htmlFor,
  comingSoon,
  badge,
  badgeClassName,
}: SettingsRowProps) {
  return (
    <div className="flex flex-col gap-2 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:gap-8">
      <label className="flex flex-col gap-1" htmlFor={htmlFor}>
        <span className="flex items-center gap-2">
          <span
            className={cn(
              "text-sm/none font-medium",
              comingSoon ? "text-secondary" : "text-primary"
            )}
          >
            {label}
          </span>
          {(comingSoon || badge) && (
            <Badge
              color="secondary"
              rounded="sm"
              size="xxs"
              className={comingSoon ? undefined : badgeClassName}
            >
              {comingSoon ? "Coming soon" : (badge ?? "")}
            </Badge>
          )}
        </span>
        {description && (
          <span className="text-xs/relaxed text-secondary">{description}</span>
        )}
      </label>
      <div className={cn("sm:shrink-0", comingSoon && "opacity-50")}>
        {control}
      </div>
    </div>
  )
}

/** A row that navigates to another settings page. */
export function SettingsNavRow({
  to,
  params,
  label,
  description,
}: {
  to: string
  params?: Record<string, string>
  label: string
  description?: string
}) {
  return (
    <Link
      to={to}
      params={params}
      className="flex items-center justify-between gap-8 px-4 py-3.5 transition-colors hover:bg-surface-level-2-hover"
    >
      <div className="flex flex-col gap-1">
        <span className="text-sm/none font-medium text-primary">{label}</span>
        {description && (
          <span className="text-xs/relaxed text-secondary">{description}</span>
        )}
      </div>
      <CaretRightIcon
        className="shrink-0 text-icon-secondary"
        size={14}
        weight="regular"
      />
    </Link>
  )
}

/** Full-width row for controls that need the whole card (editors, lists). */
export function SettingsPanel({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn("flex flex-col gap-3 p-4", className)}>{children}</div>
  )
}
