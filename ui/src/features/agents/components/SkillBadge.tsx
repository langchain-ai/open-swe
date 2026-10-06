import { splitPromptIntoSegments } from "./composer/composerMentions"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"

export function SkillBadge({ name }: { name: string }) {
  return (
    <Badge tier="quiet" tone="attention" className="align-baseline select-none">
      /{name}
    </Badge>
  )
}

export function SkillPromptText({ text }: { text: string }) {
  return splitPromptIntoSegments(text).map((segment, index) =>
    segment.type === "skill" ? (
      <SkillBadge key={index} name={segment.name} />
    ) : segment.type === "channel" ? (
      <span key={index} className="font-medium" title={segment.channelId}>
        #{segment.name}
      </span>
    ) : (
      <span key={index}>
        {segment.type === "text" ? segment.text : segment.source}
      </span>
    )
  )
}
