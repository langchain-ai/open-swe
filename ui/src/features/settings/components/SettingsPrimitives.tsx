import type { ReactNode } from "react"

import { Badge, Card, Text } from "@/components/langsmith"
import { cn } from "@/lib/utils"

interface SettingsSectionProps {
  title: string
  description?: string
  action?: ReactNode
  children: ReactNode
}

export function SettingsSection({
  title,
  description,
  action,
  children,
}: SettingsSectionProps) {
  return (
    <section className="flex flex-col gap-space-3">
      <div className="flex items-start justify-between gap-space-4">
        <div className="flex flex-col gap-space-1">
          <Text as="h2" variant="sm" weight="medium" color="primary">
            {title}
          </Text>
          {description && (
            <Text
              as="p"
              variant="xs"
              color="secondary"
              className="max-w-2xl leading-relaxed"
            >
              {description}
            </Text>
          )}
        </div>
        {action}
      </div>
      <Card
        className="divide-y divide-ls-subtle overflow-hidden p-0"
        intent="neutral"
      >
        {children}
      </Card>
    </section>
  )
}

interface SettingsRowProps {
  label: string
  description?: ReactNode
  control: ReactNode
  htmlFor?: string
  comingSoon?: boolean
}

export function SettingsRow({
  label,
  description,
  control,
  htmlFor,
  comingSoon,
}: SettingsRowProps) {
  return (
    <div className="flex flex-col gap-space-3 px-space-4 py-space-4 sm:flex-row sm:items-center sm:justify-between sm:gap-space-6">
      <label className="flex min-w-0 flex-col gap-space-1" htmlFor={htmlFor}>
        <span className="flex items-center gap-space-2">
          <Text
            as="span"
            variant="sm"
            weight="medium"
            color={comingSoon ? "secondary" : "primary"}
          >
            {label}
          </Text>
          {comingSoon && (
            <Badge color="plain" size="xxs" rounded="xs">
              Coming soon
            </Badge>
          )}
        </span>
        {description && (
          <Text
            as="span"
            variant="xs"
            color="secondary"
            className="leading-relaxed"
          >
            {description}
          </Text>
        )}
      </label>
      <div
        className={cn(
          "w-full sm:w-auto sm:shrink-0",
          comingSoon && "opacity-50"
        )}
      >
        {control}
      </div>
    </div>
  )
}

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
