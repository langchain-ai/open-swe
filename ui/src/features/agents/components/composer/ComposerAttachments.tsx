import { XIcon } from "@langchain/macaw-components/icons"
import { IconButton } from "@langchain/macaw-components/IconButton"

import { ExpandableImage } from "@/components/ImageLightbox"
import type { ImageChunk } from "@/features/agents/lib/types"
import {
  excerptSelection,
  serializeExcerpts,
  type CodeExcerpt,
} from "@/features/agents/utils/codeExcerpt"
import {
  normalizeSelection,
  type PassageReply,
} from "@/features/agents/utils/passageReply"

/**
 * Something the person pointed Open SWE at, riding ahead of their next message:
 * a passage they replied to (including one quoted from the page), an image they
 * marked up, or code lines.
 */
export type ComposerAttachment =
  | {
      kind: "reply"
      reply: PassageReply
      /** What the card calls itself; a plain reply is "Reply". */
      label?: string
      location?: string
    }
  | { kind: "image"; image: ImageChunk; comment: string }
  | { kind: "code"; excerpt: CodeExcerpt }

/** Something on the page, such as a finding or a review comment, quoted into the next message with what to do about it. */
export function quoteFromPage({
  label,
  location,
  quote,
  comment,
}: {
  label: string
  location?: string
  quote?: string
  comment: string
}): ComposerAttachment {
  return {
    kind: "reply",
    label,
    location,
    reply: {
      source: "conversation",
      before: "",
      selection: normalizeSelection(quote || location || label),
      after: "",
      comment: comment.trim(),
    },
  }
}

export interface PendingAttachment {
  id: string
  attachment: ComposerAttachment
}

/** The message with every attachment folded in: replies as replies, images as images, the rest as text. */
export function withAttachments(
  text: string,
  images: Array<ImageChunk>,
  attachments: ReadonlyArray<ComposerAttachment>
): { text: string; images: Array<ImageChunk>; replies: Array<PassageReply> } {
  const notes = attachments.flatMap((item) =>
    item.kind === "image" && item.comment ? [item.comment] : []
  )
  return {
    text: serializeExcerpts(
      [...notes, text].filter(Boolean).join("\n\n"),
      attachments.flatMap((item) =>
        item.kind === "code" ? [item.excerpt] : []
      )
    ),
    images: [
      ...attachments.flatMap((item) =>
        item.kind === "image" ? [item.image] : []
      ),
      ...images,
    ],
    replies: attachments.flatMap((item) =>
      item.kind === "reply" ? [item.reply] : []
    ),
  }
}

function excerptLines(lineLabel: string): string {
  const match = /^([LR])(\d+)(?:-(\d+))?$/.exec(lineLabel)
  if (!match) return lineLabel
  const [, side, start, end] = match
  return `${side === "L" ? "old " : ""}${end ? `${start}–${end}` : start}`
}

interface CardContent {
  label: string
  location?: string
  quote?: string
  image?: ImageChunk
  comment?: string
}

function cardContent(attachment: ComposerAttachment): CardContent {
  switch (attachment.kind) {
    case "reply": {
      const { reply, label, location } = attachment
      return {
        label: label ?? "Reply",
        location:
          location ?? (reply.file ? `${reply.file}:${reply.lines}` : undefined),
        quote: reply.selection === location ? undefined : reply.selection,
        comment: label ? undefined : reply.comment,
      }
    }
    case "image":
      return {
        label: "Reply",
        image: attachment.image,
        comment: attachment.comment,
      }
    case "code":
      return {
        label: "Ask",
        location: `${attachment.excerpt.path}:${excerptLines(attachment.excerpt.lineLabel)}`,
        quote: excerptSelection(attachment.excerpt),
      }
  }
}

/** One attachment as a card: what it points at, what it is, and a way to drop it. */
function AttachmentCard({
  attachment,
  onRemove,
}: {
  attachment: ComposerAttachment
  onRemove: () => void
}) {
  const { label, location, quote, image, comment } = cardContent(attachment)
  return (
    <li
      title={[location, quote, comment].filter(Boolean).join("\n\n")}
      className="group relative flex h-24 w-36 flex-col justify-between gap-space-1 rounded-lg border border-default bg-surface-level-2 p-space-2"
    >
      <div className="flex min-h-0 flex-col gap-0.5 border-l-2 border-default pl-space-2">
        {location && (
          <span className="truncate font-mono text-xxs text-tertiary">
            {location}
          </span>
        )}
        {image ? (
          <ExpandableImage
            src={`data:${image.mimeType};base64,${image.base64}`}
            alt={image.fileName || "Image"}
            annotatable={false}
            className="h-8 w-full rounded-sm object-cover object-top"
          />
        ) : null}
        {(comment || quote) && (
          <p
            className={
              image
                ? "line-clamp-1 text-xs [overflow-wrap:anywhere] text-secondary"
                : "line-clamp-3 text-xs [overflow-wrap:anywhere] whitespace-pre-wrap text-secondary"
            }
          >
            {comment || quote}
          </p>
        )}
      </div>
      <span className="self-start rounded-full border border-default px-space-2 text-xxs text-secondary">
        {label}
      </span>
      <IconButton
        className="absolute -top-1.5 -right-1.5 opacity-0 group-focus-within:opacity-100 group-hover:opacity-100"
        color="secondary"
        icon={XIcon}
        label={`Remove ${label.toLowerCase()}`}
        onClick={onRemove}
        round
        size="xs"
        variant="outlined"
      />
    </li>
  )
}

/** Everything riding on the next message, one card each, removable before sending. */
export function ComposerAttachmentCards({
  attachments,
  onRemove,
}: {
  attachments: ReadonlyArray<PendingAttachment>
  onRemove: (id: string) => void
}) {
  if (attachments.length === 0) return null
  return (
    <ul
      aria-label="Replies in your next message"
      className="mb-space-2 flex flex-wrap gap-space-2"
    >
      {attachments.map(({ id, attachment }) => (
        <AttachmentCard
          key={id}
          attachment={attachment}
          onRemove={() => onRemove(id)}
        />
      ))}
    </ul>
  )
}
