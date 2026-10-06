"use client";

/*
 * Rules for OnboardingDialog.
 *
 * The rules themselves are `ONBOARDING_DIALOG_RULES` below, not this comment.
 * /design renders that array verbatim beside the live pattern.
 */

import { useState, type KeyboardEvent, type ReactNode } from "react";
import { HostLink as Link } from "../host";

import { Box, Inline, Stack } from "../ui/box";
import { Button } from "../ui/button";
import { cn } from "../ui/cn";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";

const ONBOARDING_DIALOG_RULES: readonly string[] = [
  "OnboardingDialog teaches a surface. WizardDialog remains the create flow. Do not put fields in this dialog.",
  "One Dialog. A step is a visual, a title, and one or two sentences. Never a form and never a third pane.",
  "The visual is a product miniature or an image the caller owns. It is not a stock illustration and not Orb.",
  "The title uses text-title. The explanation uses text-meta. Do not borrow text-page.",
  "Skip, Escape, and finishing the last step have the same outcome: the tour is done. Do not ask again on the next reload. There is no extra close control on the stage.",
  "The primary names the next commitment: Continue, then the completeLabel the caller passed.",
  "A step change uses the same 160ms pane enter as the wizard. Forward arrives from the right, back from the left. Reduced motion is a cut.",
  "A new feature ships a new tour id and its own steps. It never appends to Welcome.",
  "A step title may link to the surface it names. The href is a product route, never an invented name.",
  "This pattern does not write storage. The host marks the tour seen.",
];

interface OnboardingStep {
  id: string;
  title: string;
  description: string;
  visual: ReactNode;
  href?: string;
}

interface OnboardingDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  eyebrow: string;
  steps: readonly OnboardingStep[];
  step: number;
  onStepChange: (step: number) => void;
  onComplete: () => void;
  completeLabel: string;
  skipLabel?: string;
}

function OnboardingDots({
  current,
  onChange,
  steps,
}: {
  current: number;
  onChange: (step: number) => void;
  steps: readonly OnboardingStep[];
}) {
  return (
    <Inline
      render={<Box role="tablist" aria-label="Tour steps" />}
      gap="xs"
      align="center"
    >
      {steps.map((item, index) => {
        const selected = index === current;
        return (
          <Box
            key={item.id}
            render={<button type="button" />}
            role="tab"
            aria-label={`${item.title}, step ${index + 1} of ${steps.length}`}
            aria-selected={selected}
            className="flex h-control-sm w-control-sm items-center justify-center rounded-compact"
            onClick={() => onChange(index)}
          >
            <Box
              aria-hidden
              className={cn(
                "h-2 rounded-badge transition-[width,background-color] duration-fast ease-out-quint",
                selected ? "w-4 bg-ink" : "w-2 bg-line-strong"
              )}
            />
          </Box>
        );
      })}
    </Inline>
  );
}

function OnboardingDialog({
  completeLabel,
  eyebrow,
  onComplete,
  onOpenChange,
  onStepChange,
  open,
  skipLabel = "Skip",
  step,
  steps,
}: OnboardingDialogProps) {
  const current = steps[step];
  const last = step === steps.length - 1;
  const [direction, setDirection] = useState<"FORWARD" | "BACK">("FORWARD");

  function goToStep(next: number) {
    if (next < 0 || next >= steps.length) return;
    setDirection(next < step ? "BACK" : "FORWARD");
    onStepChange(next);
  }

  function finish() {
    onComplete();
    onOpenChange(false);
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "ArrowRight") {
      event.preventDefault();
      if (last) finish();
      else goToStep(step + 1);
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      goToStep(step - 1);
    }
  }

  if (current === undefined) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="flex max-h-dvh min-h-0 flex-col gap-0 overflow-hidden p-0 sm:max-w-lg!"
        onKeyDown={onKeyDown}
      >
        <Box
          key={current.id}
          data-slot="onboarding-step-pane"
          data-direction={direction === "BACK" ? "back" : "forward"}
          className="overflow-hidden rounded-t-shell border-b border-line bg-muted"
        >
          {current.visual}
        </Box>
        <Stack gap="lg" className="p-5">
          <DialogHeader>
            <Box
              render={<p />}
              className="text-meta font-medium tracking-caps text-ink-subtle uppercase"
            >
              {eyebrow}
            </Box>
            <DialogTitle className="text-title font-semibold text-ink">
              {current.href !== undefined ? (
                <Link
                  href={current.href}
                  className="underline-offset-3 hover:underline"
                  onClick={finish}
                >
                  {current.title}
                </Link>
              ) : (
                current.title
              )}
            </DialogTitle>
            <DialogDescription>{current.description}</DialogDescription>
          </DialogHeader>
        </Stack>
        <DialogFooter className="mx-0 mb-0 sm:justify-between">
          <OnboardingDots
            current={step}
            steps={steps}
            onChange={goToStep}
          />
          <Inline gap="sm" align="center">
            <Button type="button" variant="ghost" onClick={finish}>
              {skipLabel}
            </Button>
            <Button
              type="button"
              onClick={() => {
                if (last) finish();
                else goToStep(step + 1);
              }}
            >
              {last ? completeLabel : "Continue"}
            </Button>
          </Inline>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export { OnboardingDialog, ONBOARDING_DIALOG_RULES };
export type { OnboardingDialogProps, OnboardingStep };
