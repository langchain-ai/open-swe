import {
  ArrowLeftIcon,
  CaretRightIcon,
} from "@langchain/macaw-components/icons"
import { Badge } from "@langchain/macaw-components/Badge"
import { Card } from "@langchain/macaw-components/Card"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { Text } from "@langchain/macaw-components/Text"
import { Link, Navigate } from "@tanstack/react-router"
import type { ReactNode } from "react"

import type { SessionUser } from "@/lib/api"
import { AppSidebar } from "@/components/AppSidebar"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

interface AppShellProps {
  user: SessionUser
  title?: string
  /** Hand the whole content area to the page, which renders its own header. */
  fill?: boolean
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
  fill,
  children,
}: AppShellProps) {
  if (fill) {
    return (
      <div className="flex h-svh overflow-hidden bg-surface-level-1 text-primary">
        <AppSidebar user={user} />
        <main className="relative flex min-w-0 flex-1">{children}</main>
      </div>
    )
  }
  return (
    <div className="flex h-svh overflow-hidden bg-surface-level-1 text-primary">
      <AppSidebar user={user} />
      <main className="relative flex-1 overflow-y-auto">
        <div
          className={cn(
            "mx-auto max-w-3xl px-space-4 pt-space-8 pb-space-9 sm:px-space-6 sm:py-space-8",
            className
          )}
        >
          {backTo && (
            <Link
              to={backTo.to}
              className="mb-space-4 inline-flex items-center gap-space-2 text-xs text-secondary hover:text-primary"
            >
              <ArrowLeftIcon size={14} weight="regular" />
              {backTo.label}
            </Link>
          )}
          <header className="mb-space-6 flex flex-wrap items-center justify-between gap-space-4">
            <div>
              <Text variant="h1">{title}</Text>
              {description && (
                <Text
                  as="p"
                  variant="sm"
                  color="secondary"
                  className="mt-space-1 max-w-2xl"
                >
                  {description}
                </Text>
              )}
            </div>
            {action}
          </header>
          <div className="flex flex-col gap-space-6">{children}</div>
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
      <main className="p-space-5">
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
    <section id={id} className="flex flex-col gap-space-3">
      <div className="flex items-start justify-between gap-space-4">
        <div className="flex flex-col gap-space-1">
          <Text as="h2" variant="h5" weight="medium">
            {title}
          </Text>
          {description && (
            <Text as="p" variant="sm" color="secondary" className="max-w-2xl">
              {description}
            </Text>
          )}
        </div>
        {action}
      </div>
      {children && (
        <Card className="divide-y divide-default overflow-hidden p-0">
          {children}
        </Card>
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
    <div className="flex flex-col gap-space-2 px-space-4 py-space-3 sm:flex-row sm:items-center sm:justify-between sm:gap-space-6">
      <label className="flex flex-col gap-space-1" htmlFor={htmlFor}>
        <span className="flex items-center gap-space-2">
          <Text
            as="span"
            variant="md"
            weight="medium"
            color={comingSoon ? "secondary" : "primary"}
          >
            {label}
          </Text>
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
          <Text as="span" variant="sm" color="secondary">
            {description}
          </Text>
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
      className="flex items-center justify-between gap-space-6 px-space-4 py-space-3 transition-colors hover:bg-surface-level-2-hover"
    >
      <div className="flex flex-col gap-space-1">
        <Text as="span" variant="md" weight="medium">
          {label}
        </Text>
        {description && (
          <Text as="span" variant="sm" color="secondary">
            {description}
          </Text>
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
    <div className={cn("flex flex-col gap-space-3 p-space-4", className)}>
      {children}
    </div>
  )
}
