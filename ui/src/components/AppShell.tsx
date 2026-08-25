import { Link } from "@tanstack/react-router"
import { ArrowLeftIcon, CaretRightIcon } from "@phosphor-icons/react"
import type { ReactNode } from "react"

import type { SessionUser } from "@/lib/api"
import { AppSidebar } from "@/components/AppSidebar"
import { cn } from "@/lib/utils"

interface AppShellProps {
  user: SessionUser
  title: string
  description?: string
  backTo?: { to: string; label: string }
  className?: string
  children: ReactNode
}

export function AppShell({
  user,
  title,
  description,
  backTo,
  className,
  children,
}: AppShellProps) {
  return (
    <div className="flex h-svh overflow-hidden bg-background text-foreground">
      <AppSidebar user={user} />
      <main className="flex-1 overflow-y-auto">
        <div
          className={cn(
            "mx-auto max-w-3xl px-4 pt-14 pb-16 sm:px-8 sm:py-12",
            className
          )}
        >
          {backTo && (
            <Link
              to={backTo.to}
              className="mb-4 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
            >
              <ArrowLeftIcon className="size-3.5" />
              {backTo.label}
            </Link>
          )}
          <header className="mb-10">
            <h1 className="font-heading text-xl font-medium tracking-tight">
              {title}
            </h1>
            {description && (
              <p className="mt-1.5 max-w-2xl text-xs text-muted-foreground">
                {description}
              </p>
            )}
          </header>
          <div className="space-y-10">{children}</div>
        </div>
      </main>
    </div>
  )
}

interface SettingsSectionProps {
  title: string
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
}

/** Label + description on the left, a single control on the right. */
export function SettingsRow({
  label,
  description,
  control,
  htmlFor,
  comingSoon,
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
          {comingSoon && (
            <span className="rounded-sm border border-border bg-muted px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">
              Coming soon
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
