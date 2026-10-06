import { Link } from "@tanstack/react-router"

import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { Box, Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { IconWell } from "@langchain/gtm-platform-design-system/ui/icon-well"
import { HELP_CLASS, LABEL_CLASS } from "@langchain/gtm-platform-design-system/ui/label"

import { Clock } from "@/components/glyphs"
import { AUTOMATION_TEMPLATES } from "@/features/automations/lib/automation-templates"
import { describeCron } from "@/features/automations/lib/cron"

/* The ChoiceCards card, as a link: picking a template opens the editor seeded with it. */
const TEMPLATE_CARD_CLASS =
  "rounded-compact border border-line-strong bg-panel p-3 outline-none hover:bg-hover focus-visible:ring-2 focus-visible:ring-primary"

export function AutomationTemplates() {
  return (
    <PageSection
      title="Start from a template"
      description="Prefilled instructions and a schedule you can tweak before saving."
    >
      <Box className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {AUTOMATION_TEMPLATES.map((template) => (
          <Link
            key={template.id}
            to="/agents/automations/new"
            search={{ template: template.id }}
            className={TEMPLATE_CARD_CLASS}
          >
            <Inline gap="sm" align="start">
              <IconWell>
                <Icon icon={template.icon} size="sm" />
              </IconWell>
              <Stack gap="xs" className="min-w-0 flex-1">
                <Box render={<span />} className={LABEL_CLASS}>
                  {template.name}
                </Box>
                <Box render={<span />} className={HELP_CLASS}>
                  {template.description}
                </Box>
                <Inline
                  render={<span />}
                  gap="xs"
                  className="pt-1 text-meta text-ink-subtle"
                >
                  <Icon icon={Clock} size="sm" />
                  {describeCron(template.schedule)}
                </Inline>
              </Stack>
            </Inline>
          </Link>
        ))}
      </Box>
    </PageSection>
  )
}
