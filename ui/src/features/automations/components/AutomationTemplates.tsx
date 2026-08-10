import { Link } from "@tanstack/react-router"
import { ClockIcon } from "@phosphor-icons/react"

import { AUTOMATION_TEMPLATES } from "@/features/automations/lib/automation-templates"
import { describeCron } from "@/features/automations/lib/cron"

export function AutomationTemplates() {
  return (
    <div className="mt-10">
      <h2 className="text-xs font-medium text-muted-foreground">
        Start from a template
      </h2>
      <p className="mt-1 text-xs text-muted-foreground/70">
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
              className="flex flex-col rounded-xl border border-border bg-card px-4 py-3 transition-colors hover:border-muted-foreground/70"
            >
              <div className="flex items-center gap-2">
                <Icon className="size-4 shrink-0 text-muted-foreground" />
                <span className="truncate text-sm font-medium text-foreground">
                  {template.name}
                </span>
              </div>
              <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
                {template.description}
              </p>
              <span className="mt-2 flex items-center gap-1 text-xs text-muted-foreground/70">
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
