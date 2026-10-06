"use client";

/*
 * Avatar, on CORE 14.
 *
 * SHAPE. Round, always, and that is the meaning rather than the decoration: a
 * circle is a person in this product. Provider marks, account logos and object
 * glyphs are square-ish things on the radius ladder, so anything round can be
 * read as "a human" without a label. There is no `shape` prop for that reason.
 *
 * SIZES. Three, taken from what the boards actually draw rather than invented:
 * `chat` 24px is the compact avatar slot, `control` 32px is the row
 * and toolbar slot (the control step of the density ladder), and `identity`
 * 48px is the record peek panel's identity block (CORE 03, 420px rail). They
 * sit on the icon ladder's top slot and its doublings (24/32/48) rather than on
 * the density ladder, because an avatar is a glyph-shaped object measured
 * against the type beside it, not a control you can click through.
 *
 * The dock's thread cards draw a 28px avatar (Page-1 board 44), which is not a
 * step here. That board is on the superseded exploration page and the survey
 * flags it as needing ratification onto Core 5; when someone ratifies it, 28 is
 * a fourth step, not a call-site override.
 *
 * INITIALS. Derived, never passed: two people with the same name must get the
 * same chip on every surface, and a caller that can pass initials is a caller
 * that will pass different ones in the row and in the panel. First letter of
 * the first word plus first letter of the last word, uppercased; one word gives
 * one letter; a name with nothing letter-like in it gives `?`. Code points, not
 * UTF-16 units, so a name that starts outside the BMP is not split in half.
 *
 * COLOUR. The fill is a hash of the name over the five state pairs, so the same
 * person is the same colour everywhere without storing anything. Two things to
 * be honest about: the pairs are named for meaning, and here they carry none --
 * an `attention`-tinted avatar is not a warning, it is Ana Torres. That is only
 * safe because the avatar wears the *only* colour in its own lane and never
 * sits where a reader is scanning tint for status; a surface where it would
 * (a risk queue whose rows are tinted) passes `tone="neutral"` and takes the
 * grey. No new palette is invented for identity: five pairs already exist, and
 * a sixth vocabulary of "avatar colours" is exactly the drift the token system
 * is for.
 *
 * IMAGE. `next/image` with `unoptimized`, following the file-chips precedent:
 * avatar sources are arbitrary provider URLs (Salesforce, Gravatar, LinkedIn),
 * the optimizer would need a `remotePatterns` entry per CRM host, and a
 * primitive cannot own next.config. A load failure falls back to the initials
 * rather than to a broken-image glyph, which is why this file is a client
 * component at all.
 */

import { HostImage as Image } from "../host";
import { useState } from "react";

import { cn } from "./cn";

type AvatarSize = "chat" | "control" | "identity";

type AvatarTone = "neutral" | "info" | "positive" | "attention" | "risk";

/* Geometry per slot: the box, and the type step that fits inside it. */
const AVATAR_SIZE_CLASS: Record<AvatarSize, string> = {
  chat: "size-6 text-meta",
  control: "size-8 text-label",
  identity: "size-12 text-title",
};

/* The intrinsic hint next/image wants; the box itself comes from the class. */
const AVATAR_PIXELS: Record<AvatarSize, number> = {
  chat: 24,
  control: 32,
  identity: 48,
};

const AVATAR_TONE_CLASS: Record<AvatarTone, string> = {
  neutral: "bg-neutral-bg text-neutral",
  info: "bg-info-bg text-info",
  positive: "bg-positive-bg text-positive",
  attention: "bg-attention-bg text-attention",
  risk: "bg-risk-bg text-risk",
};

/*
 * Hash order is part of the contract: reordering this list repaints every
 * avatar in the product, so it is a migration, not a tidy-up.
 */
const AVATAR_TONES: readonly AvatarTone[] = [
  "neutral",
  "info",
  "positive",
  "attention",
  "risk",
];

/** Shown when a name carries nothing letter-like at all (an id, an emoji, ""). */
const NO_INITIALS = "?";

function isNameLike(token: string): boolean {
  const [first] = Array.from(token);
  return first !== undefined && /[\p{L}\p{N}]/u.test(first);
}

function firstCodePoint(token: string): string {
  return (Array.from(token)[0] ?? "").toUpperCase();
}

/** First of the first word plus first of the last word, uppercased. */
function deriveInitials(name: string): string {
  const words = name.trim().split(/\s+/).filter(isNameLike);
  const first = words.at(0);
  const last = words.at(-1);
  if (first === undefined || last === undefined) {
    return NO_INITIALS;
  }
  if (words.length === 1) {
    return firstCodePoint(first);
  }
  return firstCodePoint(first) + firstCodePoint(last);
}

/*
 * djb2, reduced over the tone list. Small on purpose: the job is a stable
 * spread over five buckets, not cryptography, and it has to give the same
 * answer in the browser, in a test, and on the server.
 */
function deriveTone(seed: string): AvatarTone {
  let hash = 5381;
  for (const character of seed) {
    hash = (hash * 33 + (character.codePointAt(0) ?? 0)) % 233280;
  }
  return AVATAR_TONES[hash % AVATAR_TONES.length] ?? "neutral";
}

interface AvatarProps extends Omit<React.ComponentProps<"span">, "children"> {
  /** The person. Labels the avatar, derives the initials, and seeds the tone. */
  name: string;
  /** Ladder slot: 24 conversation / 32 row / 48 identity block. */
  size?: AvatarSize;
  /** Optional portrait; falls back to the initials if it fails to load. */
  src?: string;
  /** Pins the fill instead of hashing it, for surfaces that read tint as status. */
  tone?: AvatarTone;
}

function Avatar({
  className,
  name,
  size = "control",
  src,
  tone,
  ...props
}: AvatarProps) {
  /* Remembers which source failed, so a new portrait gets its own attempt instead of inheriting the failure. */
  const [failedSrc, setFailedSrc] = useState<string | undefined>(undefined);
  const resolvedTone = tone ?? deriveTone(name);
  const showImage = src !== undefined && src !== failedSrc;

  return (
    <span
      data-slot="avatar"
      data-size={size}
      data-tone={resolvedTone}
      role="img"
      aria-label={name}
      className={cn(
        "relative inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full font-medium select-none",
        AVATAR_SIZE_CLASS[size],
        AVATAR_TONE_CLASS[resolvedTone],
        className
      )}
      {...props}
    >
      {showImage ? (
        <Image
          data-slot="avatar-image"
          src={src}
          alt=""
          width={AVATAR_PIXELS[size]}
          height={AVATAR_PIXELS[size]}
          unoptimized
          onError={() => setFailedSrc(src)}
          className="size-full object-cover"
        />
      ) : (
        <span data-slot="avatar-initials" aria-hidden="true">
          {deriveInitials(name)}
        </span>
      )}
    </span>
  );
}

export { Avatar, deriveInitials, deriveTone };
export type { AvatarProps, AvatarSize, AvatarTone };
