import { Link } from "@tanstack/react-router"
import { ClockIcon } from "@phosphor-icons/react"

import { AUTOMATION_TEMPLATES } from "@/features/automations/lib/automation-templates"
import { describeCron } from "@/features/automations/lib/cron"

export function AutomationTemplates() {
  return (
    <div className="mt-10">
      <h2 className="text-meta font-medium text-ink-subtle">
        Start from a template
      </h2>
      <p className="mt-1 text-meta text-ink-subtle/70">
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
              className="flex flex-col rounded-control border border-line bg-panel px-4 py-3 transition-colors hover:border-ink-subtle/70"
            >
              <div className="flex items-center gap-2">
                <Icon className="size-4 shrink-0 text-ink-subtle" />
                <span className="truncate text-body font-medium text-ink">
                  {template.name}
                </span>
              </div>
              <p className="mt-1.5 text-meta leading-relaxed text-ink-subtle">
                {template.description}
              </p>
              <span className="mt-2 flex items-center gap-1 text-meta text-ink-subtle/70">
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
