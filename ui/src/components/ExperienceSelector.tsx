import { Button } from "@langchain/macaw-components/Button"
import { ButtonGroup } from "@langchain/macaw-components/ButtonGroup"
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
    <div className="px-space-4 pb-space-4" role="group" aria-label="Experience">
      <ButtonGroup
        size="md"
        color="secondary"
        variant="outlined"
        className="w-full [&>*:not(:first-child)]:border-l [&>*:not(:first-child)]:border-l-transparent"
      >
        {(
          [
            ["agent_workspace", "Work"],
            ["coding", "Coding"],
          ] as const
        ).map(([value, label]) => (
          <Button
            key={value}
            aria-pressed={experience === value}
            className={cn(
              "min-w-0 flex-1 basis-0 justify-center text-center",
              experience === value &&
                "bg-surface-level-4 font-semibold text-primary"
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
      </ButtonGroup>
    </div>
  )
}
