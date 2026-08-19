import { splitPromptIntoSegments } from "./composer/composerMentions"

export function SkillBadge({ name }: { name: string }) {
  return (
    <span className="inline-flex items-center rounded-md bg-amber-500/20 px-1.5 py-0.5 leading-tight font-medium text-amber-600 select-none dark:text-amber-300">
      /{name}
    </span>
  )
}

export function SkillPromptText({ text }: { text: string }) {
  return splitPromptIntoSegments(text).map((segment, index) =>
    segment.type === "skill" ? (
      <SkillBadge key={index} name={segment.name} />
    ) : (
      <span key={index}>
        {segment.type === "text" ? segment.text : segment.source}
      </span>
    )
  )
}
