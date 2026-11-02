import { Button } from "@langchain/macaw-components/Button"
import { CodeIcon } from "@phosphor-icons/react/dist/ssr/Code"
import { HouseIcon } from "@phosphor-icons/react/dist/ssr/House"
import { useNavigate, useRouterState } from "@tanstack/react-router"
import { useSession } from "@/lib/session"
import { useHrefLinkOptions } from "@/lib/appLocation"
import { cn } from "@/lib/utils"
import { experienceStore, useExperience } from "@/lib/experience"

export function ExperienceSelector({
  onNavigate,
}: {
  onNavigate?: () => void
}) {
  const { experience, select } = useExperience()
  const navigate = useNavigate()
  const session = useSession()
  const userId = session.data?.user_id ?? ""
  const href = useRouterState({ select: (state) => state.location.href })
  const linkOptions = useHrefLinkOptions()
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
              if (value === experience) return
              experienceStore.rememberLocation(userId, experience, href)
              const destination = experienceStore.location(userId, value)
              select(value)
              onNavigate?.()
              void navigate(linkOptions(destination))
            }}
          >
            {label}
          </Button>
        ))}
      </div>
    </div>
  )
}
