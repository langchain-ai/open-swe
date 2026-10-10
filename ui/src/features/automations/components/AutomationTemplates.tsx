import { Text } from "@langchain/macaw-components/Text"
import { Link } from "@tanstack/react-router"
import { ClockIcon } from "@phosphor-icons/react/dist/ssr/Clock"

import { AUTOMATION_TEMPLATES } from "@/features/automations/lib/automation-templates"
import { describeCron } from "@/features/automations/lib/cron"

export function AutomationTemplates() {
  return (
    <div className="mt-space-7">
      <Text as="h2" variant="sm" weight="medium" color="secondary">
        Start from a template
      </Text>
      <p className="mt-space-1 text-xs text-tertiary">
        Prefilled instructions and a schedule you can tweak before saving.
      </p>
      <div className="mt-space-3 grid gap-space-3 sm:grid-cols-2">
        {AUTOMATION_TEMPLATES.map((template) => {
          const Icon = template.icon
          return (
            <Link
              key={template.id}
              to="/agents/automations/new"
              search={{ template: template.id }}
              className="flex flex-col rounded-xl border border-default bg-surface-level-1 px-space-4 py-space-3 transition-colors hover:bg-surface-level-1-hover"
            >
              <div className="flex items-center gap-space-2">
                <Icon
                  size={16}
                  weight="regular"
                  className="shrink-0 text-icon-secondary"
                />
                <span className="truncate text-sm font-medium text-primary">
                  {template.name}
                </span>
              </div>
              <p className="mt-space-1 text-xs leading-relaxed text-secondary">
                {template.description}
              </p>
              <span className="mt-space-2 flex items-center gap-space-1 text-xs text-tertiary">
                <ClockIcon size={14} weight="regular" className="shrink-0" />
                {describeCron(template.schedule)}
              </span>
            </Link>
          )
        })}
      </div>
    </div>
  )
}
