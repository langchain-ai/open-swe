import { Link } from "@tanstack/react-router"
import { ClockIcon } from "@phosphor-icons/react"

import { AUTOMATION_TEMPLATES } from "@/lib/agents/automation-templates"
import { describeCron } from "@/lib/agents/cron"

export function AutomationTemplates() {
  return (
    <div className="mt-10">
      <h2 className="text-xs font-medium text-[var(--ui-text-muted)]">
        Start from a template
      </h2>
      <p className="mt-1 text-xs text-[var(--ui-text-dim)]">
        Prefilled instructions and a schedule you can tweak before saving.
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {AUTOMATION_TEMPLATES.map((template) => {
          const Icon = template.icon
          return (
            <Link
              key={template.id}
              to="/agents/automations/new"
              search={{ template: template.id }}
              className="flex flex-col rounded-xl border border-[var(--ui-border)] bg-[var(--ui-surface)] px-4 py-3 transition-colors hover:border-[var(--ui-text-dim)]"
            >
              <div className="flex items-center gap-2">
                <Icon className="size-4 shrink-0 text-[var(--ui-text-muted)]" />
                <span className="truncate text-sm font-medium text-[var(--ui-text)]">
                  {template.name}
                </span>
              </div>
              <p className="mt-1.5 text-xs leading-relaxed text-[var(--ui-text-muted)]">
                {template.description}
              </p>
              <span className="mt-2 flex items-center gap-1 text-xs text-[var(--ui-text-dim)]">
                <ClockIcon className="size-3.5 shrink-0" />
                {describeCron(template.schedule)}
              </span>
            </Link>
          )
        })}
      </div>
    </div>
  )
}
