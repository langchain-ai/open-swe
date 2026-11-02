import { Button } from "@langchain/macaw-components/Button"
import { CodeIcon } from "@phosphor-icons/react/dist/ssr/Code"
import { HouseIcon } from "@phosphor-icons/react/dist/ssr/House"
import { useNavigate } from "@tanstack/react-router"
import { cn } from "@/lib/utils"
import { useDesktopExperience } from "@/lib/experience"

export function ExperienceSelector({
  onNavigate,
}: {
  onNavigate?: () => void
}) {
  const { experience, select } = useDesktopExperience()
  const navigate = useNavigate()
  if (!experience) return null
  return (
    <div className="px-space-2 pb-space-4">
      <div
        className="flex rounded-lg bg-surface-level-3"
        role="group"
        aria-label="Experience"
      >
        {(
          [
            ["agent_workspace", "Home", HouseIcon],
            ["coding", "Code", CodeIcon],
          ] as const
        ).map(([value, label, Icon]) => (
          <Button
            key={value}
            size="md"
            color="secondary"
            variant="plain"
            leftDecorator={Icon}
            aria-pressed={experience === value}
            className={cn(
              "min-w-0 flex-1 basis-0 rounded-lg text-center",
              experience === value &&
                "border-strong bg-surface-level-4 text-primary hover:bg-surface-level-4 dark:hover:bg-surface-level-4"
            )}
            onClick={() => {
              select(value)
              onNavigate?.()
              void navigate({ to: "/agents", search: {} })
            }}
          >
            {label}
          </Button>
        ))}
      </div>
    </div>
  )
}
