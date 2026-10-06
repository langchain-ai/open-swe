/*
 * A consumer probe: mounts a page built the way a consumer would build one, in
 * jsdom, with no provider mounted.
 *
 * The no-provider case is the one worth testing. Every host slot has a default,
 * so a consumer who forgets the provider should still get a working system
 * rather than a crash, and that promise is only true if something checks it.
 *
 * Not published: `files` in package.json ships src and agent only.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { Badge } from "../src/ui/badge";
import { Box, Inline, Stack } from "../src/ui/box";
import { Button } from "../src/ui/button";
import { AlertTriangle, ArrowUpRight } from "../src/ui/glyphs";
import { Icon } from "../src/ui/icon";
import { ConfirmableAction } from "../src/patterns/confirmable-action";
import { EmptyState } from "../src/patterns/empty-state";
import { PageFrame, PageSection } from "../src/patterns/page-frame";
import { StateNotice } from "../src/patterns/state-notice";
import { DesignSystemProvider, HostLink } from "../src/host";

afterEach(cleanup);

describe("consumer composition", () => {
  it("renders a page frame, its sections and their controls with no provider", () => {
    render(
      <PageFrame title="Runs" description="Every run this week.">
        <PageSection title="Controls">
          <Stack gap="md">
            <Inline gap="sm">
              <Button>Start a run</Button>
              <Badge>Passing</Badge>
              <Icon icon={ArrowUpRight} size="md" label="Open" />
            </Inline>
            <StateNotice
              tone="ATTENTION"
              icon={AlertTriangle}
              title="Two runs need review"
              description="They finished while you were away."
            />
            <ConfirmableAction
              trigger={<Button variant="outline">Delete run</Button>}
              title="Delete this run?"
              description="The transcript goes with it."
              confirmLabel="Delete run"
              onConfirm={async () => undefined}
            />
          </Stack>
        </PageSection>
        <PageSection title="Empty">
          <Box padding="lg">
            <EmptyState title="No runs yet" description="Start one to see it here." />
          </Box>
        </PageSection>
      </PageFrame>,
    );

    expect(screen.getByRole("heading", { level: 1, name: "Runs" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start a run" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete run" })).toBeTruthy();
    expect(screen.getByText("Two runs need review")).toBeTruthy();
    expect(screen.getByText("No runs yet")).toBeTruthy();
  });

  it("spends tokens rather than raw colours on the surfaces it paints", () => {
    const { container } = render(
      <Box bg="panel" padding="md" radius="panel" ink="ink-subtle">
        inside
      </Box>,
    );
    const html = container.innerHTML;
    expect(html).toContain("bg-panel");
    expect(html).toContain("text-ink-subtle");
    expect(html).not.toMatch(/#[0-9a-fA-F]{6}/);
  });

  it("falls back to an anchor when no link component is supplied", () => {
    render(<HostLink href="/runs">Runs</HostLink>);
    const link = screen.getByRole("link", { name: "Runs" });
    expect(link.tagName).toBe("A");
    expect(link.getAttribute("href")).toBe("/runs");
  });

  it("uses the host's link component when one is supplied", () => {
    function RouterLink({ href, children }: { href: string; children?: React.ReactNode }) {
      return (
        <a data-router="tanstack" href={href}>
          {children}
        </a>
      );
    }
    render(
      <DesignSystemProvider link={RouterLink}>
        <HostLink href="/runs">Runs</HostLink>
      </DesignSystemProvider>,
    );
    expect(screen.getByRole("link", { name: "Runs" }).dataset.router).toBe("tanstack");
  });
});
