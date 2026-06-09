import { Link } from "@tanstack/react-router";
import { ArrowLeftIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";

import type { SessionUser } from "@/lib/api";
import { AppSidebar } from "@/components/AppSidebar";
import { cn } from "@/lib/utils";

interface AppShellProps {
  user: SessionUser;
  title: string;
  description?: string;
  backTo?: { to: string; label: string };
  className?: string;
  children: ReactNode;
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
        <div className={cn("mx-auto max-w-3xl px-4 pt-14 pb-6 sm:px-8 sm:py-10", className)}>
          {backTo && (
            <Link
              to={backTo.to}
              className="mb-4 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
            >
              <ArrowLeftIcon className="size-3.5" />
              {backTo.label}
            </Link>
          )}
          <header className="mb-8">
            <h1 className="font-heading text-lg font-medium">{title}</h1>
            {description && (
              <p className="mt-1 text-xs text-muted-foreground">{description}</p>
            )}
          </header>
          <div className="space-y-8">{children}</div>
        </div>
      </main>
    </div>
  );
}

interface SettingsSectionProps {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
}

export function SettingsSection({ title, description, action, children }: SettingsSectionProps) {
  return (
    <section className="space-y-3">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {title}
          </h2>
          {description && (
            <p className="mt-0.5 text-xs text-muted-foreground/80">{description}</p>
          )}
        </div>
        {action}
      </div>
      <div className="rounded-lg border border-border bg-card">{children}</div>
    </section>
  );
}

interface SettingsRowProps {
  label: string;
  description?: string;
  control: ReactNode;
  htmlFor?: string;
  comingSoon?: boolean;
}

export function SettingsRow({
  label,
  description,
  control,
  htmlFor,
  comingSoon,
}: SettingsRowProps) {
  return (
    <div className="flex flex-col gap-2 border-b border-border px-4 py-3 last:border-b-0 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
      <label className="flex flex-col gap-0.5" htmlFor={htmlFor}>
        <span className="flex items-center gap-2">
          <span
            className={`text-xs font-medium ${
              comingSoon ? "text-muted-foreground" : "text-foreground"
            }`}
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
          <span className="text-xs text-muted-foreground">{description}</span>
        )}
      </label>
      <div className={`sm:shrink-0 ${comingSoon ? "opacity-50" : ""}`}>{control}</div>
    </div>
  );
}
