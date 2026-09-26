import { Link } from "@tanstack/react-router"
import { ArrowLeftIcon, CaretRightIcon } from "@phosphor-icons/react"
import type { ReactNode } from "react"

import type { SessionUser } from "@/lib/api"
import { AppSidebar } from "@/components/AppSidebar"
import { Skeleton } from "@/components/ui/skeleton"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

interface AppShellProps {
  user: SessionUser
  title: ReactNode
  action?: ReactNode
  description?: string
  backTo?: { to: string; label: string }
  className?: string
  children: ReactNode
}

export function PageHeader({
  title,
  description,
  action,
  backTo,
  className,
}: {
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  backTo?: { to: string; label: string }
  className?: string
}) {
  return (
    <>
      {backTo && (
        <Link
          to={backTo.to}
          className="mb-4 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeftIcon className="size-3.5" />
          {backTo.label}
        </Link>
      )}
      <header
        className={cn(
          "mb-10 flex flex-wrap items-center justify-between gap-4",
          className
        )}
      >
        <div>
          <h1 className="font-heading text-xl font-medium tracking-tight">
            {title}
          </h1>
          {description && (
            <p className="mt-1.5 max-w-2xl text-xs text-muted-foreground">
              {description}
            </p>
          )}
        </div>
        {action}
      </header>
    </>
  )
}

const PAGE_CONTAINER = "mx-auto max-w-3xl px-4 pt-14 pb-16 sm:px-8 sm:py-12"

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
    <div className="flex h-svh overflow-hidden bg-background text-foreground">
      <AppSidebar user={user} />
      <main className="relative flex-1 overflow-y-auto">
        <div className={cn(PAGE_CONTAINER, className)}>
          <PageHeader
            action={action}
            backTo={backTo}
            description={description}
            title={title}
          />
          <div className="space-y-10">{children}</div>
        </div>
      </main>
    </div>
  )
}

/** AppShell that resolves the session itself: skeleton while loading, login redirect when signed out. */
export function AuthedAppShell({
  action,
  children,
  className,
  ...props
}: Omit<AppShellProps, "user" | "action" | "children"> & {
  action?: ReactNode | ((user: SessionUser) => ReactNode)
  children: (user: SessionUser) => ReactNode
}) {
  const session = useSession()
  if (session.isLoading) {
    return (
      <div className="flex h-svh overflow-hidden bg-background">
        <main className="relative flex-1 overflow-y-auto">
          <div className={cn(PAGE_CONTAINER, className)}>
            <Skeleton className="mb-10 h-7 w-48" />
            <Skeleton className="h-40 w-full" />
          </div>
        </main>
      </div>
    )
  }
  if (!session.data) return <RequireLogin />
  const user = session.data
  return (
    <AppShell
      {...props}
      action={typeof action === "function" ? action(user) : action}
      className={className}
      user={user}
    >
      {children(user)}
    </AppShell>
  )
}

interface SettingsSectionProps {
  title: ReactNode
  description?: string
  action?: ReactNode
  children: ReactNode
}

/** A titled group of rows rendered as a single card. */
export function SettingsSection({
  title,
  description,
  action,
  children,
}: SettingsSectionProps) {
  return (
    <section className="space-y-3">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-medium text-foreground">{title}</h2>
          {description && (
            <p className="mt-1 max-w-2xl text-xs text-muted-foreground">
              {description}
            </p>
          )}
        </div>
        {action}
      </div>
      <div className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
        {children}
      </div>
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
}

/** Label + description on the left, a single control on the right. */
export function SettingsRow({
  label,
  description,
  control,
  htmlFor,
  comingSoon,
  badge,
}: SettingsRowProps) {
  return (
    <div className="flex flex-col gap-2 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:gap-8">
      <label className="flex flex-col gap-1" htmlFor={htmlFor}>
        <span className="flex items-center gap-2">
          <span
            className={cn(
              "text-sm/none font-medium",
              comingSoon ? "text-muted-foreground" : "text-foreground"
            )}
          >
            {label}
          </span>
          {(comingSoon || badge) && (
            <span className="rounded-sm border border-border bg-muted px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">
              {comingSoon ? "Coming soon" : badge}
            </span>
          )}
        </span>
        {description && (
          <span className="text-xs/relaxed text-muted-foreground">
            {description}
          </span>
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
      className="flex items-center justify-between gap-8 px-4 py-3.5 transition-colors hover:bg-muted/40"
    >
      <div className="flex flex-col gap-1">
        <span className="text-sm/none font-medium text-foreground">
          {label}
        </span>
        {description && (
          <span className="text-xs/relaxed text-muted-foreground">
            {description}
          </span>
        )}
      </div>
      <CaretRightIcon className="size-3.5 shrink-0 text-muted-foreground" />
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
