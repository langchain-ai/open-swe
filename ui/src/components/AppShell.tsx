import type { ReactNode } from "react";

import type { SessionUser } from "@/lib/api";
import { AppSidebar } from "@/components/AppSidebar";

interface AppShellProps {
  user: SessionUser;
  title: string;
  description?: string;
  children: ReactNode;
}

export function AppShell({ user, title, description, children }: AppShellProps) {
  return (
    <div className="flex min-h-svh bg-background text-foreground">
      <AppSidebar user={user} />
      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-8 py-10">
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
}

export function SettingsRow({ label, description, control, htmlFor }: SettingsRowProps) {
  return (
    <div className="flex items-center justify-between gap-6 border-b border-border px-4 py-3 last:border-b-0">
      <label className="flex flex-col gap-0.5" htmlFor={htmlFor}>
        <span className="text-xs font-medium text-foreground">{label}</span>
        {description && (
          <span className="text-xs text-muted-foreground">{description}</span>
        )}
      </label>
      <div className="shrink-0">{control}</div>
    </div>
  );
}
