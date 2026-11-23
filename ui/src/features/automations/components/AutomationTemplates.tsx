import { Link } from "@tanstack/react-router"
import { ClockIcon } from "@phosphor-icons/react/dist/ssr/Clock"

import { AutomationIconTile } from "@/features/automations/components/AutomationIconTile"
import { AUTOMATION_TEMPLATES } from "@/features/automations/lib/automation-templates"
import { describeCron } from "@/features/automations/lib/cron"
import { cn } from "@/lib/utils"

/** Prefilled automations to start from; each opens the editor to tweak before saving. */
export function AutomationTemplates({ className }: { className?: string }) {
  return (
    <ul
      aria-label="Automation templates"
      className={cn(
        "grid gap-x-space-4 gap-y-space-1 sm:grid-cols-2",
        className
      )}
    >
      {AUTOMATION_TEMPLATES.map((template) => (
        <li key={template.id}>
          <Link
            to="/agents/automations/new"
            search={{ template: template.id }}
            className="group flex h-full items-start gap-space-3 rounded-lg px-space-3 py-space-3 transition-colors hover:bg-surface-level-1-hover"
          >
            <AutomationIconTile icon={template.icon} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-primary">
                {template.name}
              </div>
              <p className="mt-0.5 text-xs leading-relaxed text-secondary">
                {template.description}
              </p>
              <span className="mt-space-1 flex items-center gap-space-1 text-xs text-tertiary">
                <ClockIcon size={14} weight="regular" className="shrink-0" />
                {describeCron(template.schedule)}
              </span>
            </div>
          </Link>
        </li>
      ))}
    </ul>
  )
}
