"use client";

/*
 * Rules for ProviderMark.
 *
 * The rules themselves are `PROVIDER_MARK_RULES` below, not this comment.
 * Second sanctioned brand module beside `ui/logo.tsx`. Always native brand
 * colour inside `IconWell` — never mono, never a bare logo (`surface-decisions`
 * §5, updated 2026-08-10).
 */

import { cn } from "../ui/cn";
import { Globe } from "../ui/glyphs";
import { Icon } from "../ui/icon";
import { IconWell } from "../ui/icon-well";
import {
  ProviderLogo,
  isProviderLogoId,
} from "../ui/provider-logos";

const PROVIDER_MARK_RULES: readonly string[] = [
  "Provider identity is a real brand mark in an IconWell, not an initial tile and not a bare SVG. Inbox rows, tool activity, receipts, and thread-rail HoverCard previews name the channel with this module. The thread-rail row itself does not: a 24px well grows the 32px control rung, so Slack origin there is a leading 16px muted well.",
  "Native brand colour is always on for ProviderMark. The one sanctioned mono exception is dense Settings feature rows, which use `ProviderLogo branded={false}` under SETTINGS_PRODUCT_RULES / surface-decisions §8e. Do not invent a second mono path.",
  "The well is 24px (`IconWell`); the logo is 14px inside it. Growing or shrinking either invents a second density.",
  "Unknown providers and `web` fall back to the Globe glyph in the same well. Inventing a new brand colour at a call site is how the palette drifts.",
];

type ProviderId =
  | "salesforce"
  | "gmail"
  | "linkedin"
  | "slack"
  | "calendar"
  | "google-docs"
  | "notion"
  | "linear"
  | "pylon"
  | "hex"
  | "web"
  | (string & {});

const PROVIDER_LABEL: Record<string, string> = {
  calendar: "Google Calendar",
  "google-docs": "Google Docs",
  gmail: "Gmail",
  hex: "Hex",
  linkedin: "LinkedIn",
  linear: "Linear",
  notion: "Notion",
  pylon: "Pylon",
  salesforce: "Salesforce",
  slack: "Slack",
  web: "Web",
};

interface ProviderMarkProps {
  provider: ProviderId;
  className?: string;
  label?: string;
  /** Test hook forwarded to the well. */
  "data-testid"?: string;
}

/**
 * Channel mark for rails, rows, tool activity, and receipts.
 */
function ProviderMark({
  className,
  label,
  provider,
  "data-testid": testId,
}: ProviderMarkProps) {
  const resolvedLabel = label ?? PROVIDER_LABEL[provider] ?? provider;

  return (
    <IconWell
      data-slot="provider-mark"
      data-provider={provider}
      data-testid={testId}
      label={resolvedLabel}
      className={cn(className)}
    >
      {isProviderLogoId(provider) ? (
        <ProviderLogo provider={provider} title={resolvedLabel} />
      ) : (
        <Icon
          icon={Globe}
          size="sm"
          label={resolvedLabel}
          className="text-info"
        />
      )}
    </IconWell>
  );
}

export { ProviderMark, PROVIDER_MARK_RULES };
export type { ProviderId, ProviderMarkProps };
