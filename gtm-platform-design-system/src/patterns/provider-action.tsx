"use client";

/*
 * Rules for ProviderAction.
 *
 * An outbound provider hop is a compact outline control with that
 * provider's logo and a verb. Alerts, Accounts peeks, and Inbox use this
 * instead of a second button-plus-icon local. The href is a server
 * https URL. The browser never invents one from an id.
 */

import type { MouseEvent } from "react";

import { Button } from "../ui/button";
import { ProviderLogo, type ProviderLogoId } from "../ui/provider-logos";

const PROVIDER_ACTION_RULES: readonly string[] = [
  "A provider hop is a compact outline control with the shipped ProviderLogo and a verb. Do not draw a second local Salesforce or LinkedIn button.",
  "The href is a server https URL. Never assemble one from a Salesforce id or a LinkedIn public id in the browser.",
  "Open in a new tab with rel=noopener noreferrer. Stop row clicks so a hop inside a list or peek does not also select the row.",
];

interface ProviderActionProps {
  href: string;
  label: string;
  provider: ProviderLogoId;
}

function stopRow(event: MouseEvent<HTMLAnchorElement>) {
  event.stopPropagation();
}

function ProviderAction({
  href,
  label,
  provider,
}: ProviderActionProps) {
  return (
    <Button
      variant="outline"
      size="compact"
      nativeButton={false}
      role="link"
      aria-label={label}
      render={
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          onClick={stopRow}
        />
      }
      data-provider={provider}
      data-slot="provider-action"
    >
      <ProviderLogo provider={provider} className="size-3.5" />
      {label}
    </Button>
  );
}

export { ProviderAction, PROVIDER_ACTION_RULES };
export type { ProviderActionProps };
