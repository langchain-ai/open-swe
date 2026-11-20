import { Button } from "@langchain/macaw-components/Button"
import { IconButton } from "@langchain/macaw-components/IconButton"
import type { IconComponent } from "@langchain/macaw-components/utils/icon-types"
import { PushPinIcon } from "@phosphor-icons/react/dist/ssr/PushPin"

import type { PullRequestRef } from "../lib/pullRequest"
import { usePin } from "../lib/queries"
import { useUsage } from "../lib/usage"

/** Pins a pull request to the inbox's Pinned tab, or unpins it; `compact` shows only the pin. */
export function PinButton({
  pr,
  title,
  compact = false,
}: {
  pr: PullRequestRef
  title: string
  compact?: boolean
}) {
  const { pinned, ready, toggle: change } = usePin(pr, title)
  const record = useUsage()
  const toggle = () => {
    record(pinned ? "unpin" : "pin")
    change()
  }
  const label = pinned ? "Unpin" : "Pin"
  if (compact)
    return (
      <IconButton
        size="sm"
        color="secondary"
        variant="plain"
        icon={PushPinIcon}
        iconWeight={pinned ? "fill" : "regular"}
        label={label}
        aria-pressed={pinned}
        disabled={!ready}
        onClick={toggle}
      />
    )
  return (
    <Button
      size="sm"
      color="secondary"
      variant="outlined"
      leftDecorator={pinned ? PinnedIcon : PushPinIcon}
      aria-pressed={pinned}
      disabled={!ready}
      onClick={toggle}
    >
      {pinned ? "Pinned" : "Pin"}
    </Button>
  )
}

/** The pin, filled while pinned; Button's decorators take no weight of their own. */
const PinnedIcon: IconComponent = (props) => (
  <PushPinIcon {...props} weight="fill" />
)
