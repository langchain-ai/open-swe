import { Link } from "@tanstack/react-router"
import { ArrowLeftIcon } from "@phosphor-icons/react"
import type { ReactNode } from "react"

import type { SessionUser } from "@/lib/api"
import { AppSidebar } from "@/components/AppSidebar"
import { PageHeader } from "@/components/patterns/page-header"
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
          <PageHeader
            title={title}
            description={description}
            className="mb-10"
          />
          <div className="space-y-10">{children}</div>
        </div>
      </main>
    </div>
  )
}
