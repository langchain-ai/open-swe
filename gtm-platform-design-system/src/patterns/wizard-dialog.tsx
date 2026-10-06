"use client";

/*
 * WizardDialog is the one in-product create flow: a compact progress trail,
 * one scroll body, and one footer whose primary names the next commitment.
 */

import { Fragment, useRef, useState, type FormEvent, type ReactNode } from "react";

import { ConfirmableAction } from "./confirmable-action";
import { SECTION_STACK_GAP } from "./form-field";
import { Box, Inline, Stack } from "../ui/box";
import { Button } from "../ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { ArrowLeft, Check } from "../ui/glyphs";
import { Icon } from "../ui/icon";
import { LABEL_CLASS } from "../ui/label";
import { Progress } from "../ui/progress";
import { ScrollAreaBody } from "../ui/scroll-area";

const WIZARD_DIALOG_RULES: readonly string[] = [
  "Use one Dialog, one scroll body, and one footer. A branch changes the current step; it never opens a second dialog.",
  "Compact is for one to four steps. Guided is for five to eight sections: a stable desktop rail yields to the current step body, while narrow screens keep Step N of M above the body.",
  "The active step is announced and future steps stay unavailable until the current step validates. Completed steps may be revisited.",
  "The primary button names its effect: Continue while moving, then Create campaign or Create play. Never Submit or OK. After a write lands and a later step fails, the primary becomes Open campaign or Open play.",
  "Keys, source types, schema names, and other wire language never appear. Derived identifiers stay behind the form.",
  "The desktop trail is one line per step: number and name. Descriptions live in the step body. Space sits between steps; the trail never packs two lines into a control-height button.",
  "At narrow widths the trail becomes Step N of M plus the current name. It never scrolls horizontally.",
  "Step and branch changes present immediately, including keyboard-submitted forms. Direction is semantic state, not a reason to animate frequent input.",
  "A last-step fork uses ChoiceCards. Each card opens one section. Returning to the cards is Back. Do not stack Link existing and Create new in one view.",
  "The wizard is a form, so it spends text-label and text-meta only. It never borrows text-page. Sibling sections use SECTION_STACK_GAP. Do not restyle the step or branch gap at a call site.",
  "A long guided form may offer Save and close as one secondary action. Back remains navigation, Continue remains the primary, and Cancel remains a terminal host action rather than a synonym for closing.",
];

type WizardLayout = "COMPACT" | "GUIDED";

interface WizardSecondaryAction {
  label: string;
  onAction: () => Promise<void> | void;
  disabled?: boolean;
  loading?: boolean;
}

interface WizardCancelAction {
  label: string;
  onAction: () => void;
  disabled?: boolean;
}

interface WizardStep {
  id: string;
  title: string;
  description: string;
}

interface WizardDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  steps: readonly WizardStep[];
  step: number;
  onStepChange: (step: number) => void;
  onSubmit: () => Promise<void>;
  validateStep?: (step: number) => boolean;
  canContinue: boolean;
  submitting: boolean;
  completeLabel: string;
  layout?: WizardLayout;
  progressLabel?: string;
  cancelAction?: WizardCancelAction;
  secondaryAction?: WizardSecondaryAction;
  onDiscard?: () => void;
  recover?: {
    label: string;
    onRecover: () => void;
  };
  children: ReactNode;
}

function WizardMobileProgress({
  current,
  progressLabel,
  steps,
}: {
  current: number;
  progressLabel: string;
  steps: readonly WizardStep[];
}) {
  const active = steps[current];
  return (
    <Stack gap="xs" className="md:hidden">
      <Inline justify="between" gap="sm">
        <Box render={<span />} className={LABEL_CLASS}>
          {`Step ${current + 1} of ${steps.length}`}
        </Box>
        <Box render={<span />} className="truncate text-label text-ink-muted">
          {active?.title}
        </Box>
      </Inline>
      <Progress
        label={progressLabel}
        value={current + 1}
        max={steps.length}
      />
    </Stack>
  );
}

function WizardCompactTrail({
  availableThrough,
  current,
  onChange,
  progressLabel,
  steps,
}: {
  availableThrough: number;
  current: number;
  onChange: (step: number) => void;
  progressLabel: string;
  steps: readonly WizardStep[];
}) {
  return (
    <Stack gap="sm">
      <WizardMobileProgress current={current} progressLabel={progressLabel} steps={steps} />
      <Inline
        render={<Box role="navigation" aria-label={progressLabel} />}
        gap="md"
        align="center"
        className="hidden md:flex"
      >
        {steps.map((item, index) => {
          const completed = index < availableThrough;
          const selected = index === current;
          return (
            <Fragment key={item.id}>
              {index > 0 ? <Box className="h-px min-w-4 flex-1 bg-line" aria-hidden /> : null}
              <Button
                type="button"
                variant={selected ? "outline" : "ghost"}
                disabled={index > availableThrough}
                aria-current={selected ? "step" : undefined}
                className="shrink-0"
                onClick={() => onChange(index)}
              >
                <Box render={<span />} className="sr-only">
                  {completed ? "Completed. " : selected ? "Current step. " : ""}
                </Box>
                <Box
                  className="flex size-4 shrink-0 items-center justify-center rounded-tick bg-muted text-meta text-ink-subtle"
                  aria-hidden
                >
                  {completed ? <Icon icon={Check} size="sm" /> : index + 1}
                </Box>
                {item.title}
              </Button>
            </Fragment>
          );
        })}
      </Inline>
    </Stack>
  );
}

function WizardGuidedTrail({
  availableThrough,
  current,
  onChange,
  progressLabel,
  steps,
}: {
  availableThrough: number;
  current: number;
  onChange: (step: number) => void;
  progressLabel: string;
  steps: readonly WizardStep[];
}) {
  return (
    <Stack
      render={<Box role="navigation" aria-label={progressLabel} />}
      gap="xs"
      className="hidden border-r border-line p-3 md:flex"
    >
      {steps.map((item, index) => {
        const completed = index < availableThrough;
        const selected = index === current;
        return (
          <Button
            key={item.id}
            type="button"
            variant={selected ? "outline" : "ghost"}
            disabled={index > availableThrough}
            aria-current={selected ? "step" : undefined}
            className="w-full justify-start"
            onClick={() => onChange(index)}
          >
            <Box
              className="flex size-4 shrink-0 items-center justify-center rounded-tick bg-muted text-meta text-ink-subtle"
              aria-hidden
            >
              {completed ? <Icon icon={Check} size="sm" /> : index + 1}
            </Box>
            <Box render={<span />} className="min-w-0 truncate text-left">
              {item.title}
            </Box>
            <Box render={<span />} className="sr-only">
              {completed ? " Completed" : selected ? " Current step" : ""}
            </Box>
          </Button>
        );
      })}
    </Stack>
  );
}

function WizardStepPane({
  children,
  direction,
  label,
}: {
  children: ReactNode;
  direction: "FORWARD" | "BACK";
  label: string;
}) {
  return (
    <Stack
      data-slot="wizard-step-pane"
      data-direction={direction === "BACK" ? "back" : "forward"}
      role="region"
      gap={SECTION_STACK_GAP}
      className="p-4"
      aria-label={label}
    >
      {children}
    </Stack>
  );
}

function WizardBranchPane({ children }: { children: ReactNode }) {
  return (
    <Stack data-slot="wizard-branch-pane" gap={SECTION_STACK_GAP}>
      {children}
    </Stack>
  );
}

function WizardBranchBack({ onClick }: { onClick: () => void }) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="compact"
      data-slot="wizard-branch-back"
      className="self-start"
      onClick={onClick}
    >
      <Icon icon={ArrowLeft} size="sm" />
      Back
    </Button>
  );
}

function WizardDialog({
  canContinue,
  cancelAction,
  children,
  completeLabel,
  description,
  layout = "COMPACT",
  onOpenChange,
  onDiscard,
  onStepChange,
  onSubmit,
  open,
  progressLabel = "Creation progress",
  recover,
  secondaryAction,
  step,
  steps,
  submitting,
  title,
  validateStep,
}: WizardDialogProps) {
  const finalStep = step === steps.length - 1;
  const submittingRef = useRef(false);
  const [direction, setDirection] = useState<"FORWARD" | "BACK">("FORWARD");
  const [maxReached, setMaxReached] = useState(step);
  const availableThrough = Math.max(step, maxReached);

  function goToStep(next: number) {
    setDirection(next < step ? "BACK" : "FORWARD");
    setMaxReached((current) => Math.max(current, next));
    onStepChange(next);
  }

  function changeOpen(next: boolean) {
    if (!next) setMaxReached(0);
    onOpenChange(next);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    // A confirmation rendered through a portal still bubbles through this form in React.
    if (event.target !== event.currentTarget) return;
    event.preventDefault();
    if (recover !== undefined) {
      recover.onRecover();
      return;
    }
    if (!canContinue || submitting || submittingRef.current) return;
    if (validateStep !== undefined && !validateStep(step)) return;
    if (!finalStep) {
      goToStep(step + 1);
      return;
    }
    submittingRef.current = true;
    void onSubmit().finally(() => {
      submittingRef.current = false;
    });
  }

  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogContent
        showCloseButton={!submitting}
        aria-busy={submitting}
        data-layout={layout === "GUIDED" ? "guided" : "compact"}
        className={
          layout === "GUIDED"
            ? "flex max-h-dvh min-h-0 flex-col gap-0 overflow-hidden p-0 sm:max-w-work!"
            : "flex max-h-dvh min-h-0 flex-col gap-0 overflow-hidden p-0 sm:max-w-reading!"
        }
      >
        <Stack
          render={<form noValidate onSubmit={submit} />}
          gap="none"
          className="min-h-0 flex-1 overflow-hidden"
        >
          <Stack gap="md" className="shrink-0 p-4">
            <DialogHeader>
              <DialogTitle>{title}</DialogTitle>
              <DialogDescription>{description}</DialogDescription>
            </DialogHeader>
            {layout === "GUIDED" ? (
              <WizardMobileProgress current={step} progressLabel={progressLabel} steps={steps} />
            ) : (
              <WizardCompactTrail
                availableThrough={availableThrough}
                current={step}
                steps={steps}
                progressLabel={progressLabel}
                onChange={goToStep}
              />
            )}
          </Stack>

          {layout === "GUIDED" ? (
            <Box className="grid min-h-0 flex-1 grid-cols-1 border-t border-line md:grid-cols-5">
              <WizardGuidedTrail
                availableThrough={availableThrough}
                current={step}
                steps={steps}
                progressLabel={progressLabel}
                onChange={goToStep}
              />
              <ScrollAreaBody overflow="vertical" className="min-h-0 md:col-span-4">
                <Box render={<p aria-live="polite" />} className="sr-only">
                  {`Step ${step + 1} of ${steps.length}, ${steps[step]?.title ?? ""}`}
                </Box>
                <WizardStepPane
                  key={step}
                  direction={direction}
                  label={`Step ${step + 1} of ${steps.length}, ${steps[step]?.title ?? ""}`}
                >
                  {children}
                </WizardStepPane>
              </ScrollAreaBody>
            </Box>
          ) : (
            <ScrollAreaBody overflow="vertical" className="border-t border-line">
              <Box render={<p aria-live="polite" />} className="sr-only">
                {`Step ${step + 1} of ${steps.length}, ${steps[step]?.title ?? ""}`}
              </Box>
              <WizardStepPane
                key={step}
                direction={direction}
                label={`Step ${step + 1} of ${steps.length}, ${steps[step]?.title ?? ""}`}
              >
                {children}
              </WizardStepPane>
            </ScrollAreaBody>
          )}

          <DialogFooter className="mx-0 mb-0 shrink-0 sm:justify-between">
            <Inline gap="sm" align="center">
              <Button
                type="button"
                variant="outline"
                disabled={step === 0 || submitting}
                onClick={() => goToStep(step - 1)}
              >
                Back
              </Button>
              {cancelAction === undefined ? null : (
                <Button
                  type="button"
                  variant="ghost"
                  disabled={cancelAction.disabled === true || submitting}
                  onClick={cancelAction.onAction}
                >
                  {cancelAction.label}
                </Button>
              )}
            </Inline>
            <Inline gap="sm" align="center">
              {onDiscard === undefined || recover !== undefined ? null : (
                <ConfirmableAction
                  trigger={<Button type="button" variant="ghost" disabled={submitting}>Discard draft</Button>}
                  title="Discard this draft?"
                  description="Your unfinished entries will be removed. You can start again with a blank form."
                  confirmLabel="Discard draft"
                  onConfirm={async () => { onDiscard(); }}
                />
              )}
              {secondaryAction === undefined ? null : (
                <Button
                  type="button"
                  variant="ghost"
                  disabled={secondaryAction.disabled === true || submitting}
                  loading={secondaryAction.loading === true}
                  onClick={() => void secondaryAction.onAction()}
                >
                  {secondaryAction.label}
                </Button>
              )}
              <Button
                type="submit"
                disabled={recover === undefined && (!canContinue || submitting)}
                loading={recover === undefined && submitting}
              >
                {recover !== undefined
                  ? recover.label
                  : finalStep
                    ? completeLabel
                    : "Continue"}
              </Button>
            </Inline>
          </DialogFooter>
        </Stack>
      </DialogContent>
    </Dialog>
  );
}

export { WizardBranchBack, WizardBranchPane, WizardDialog, WIZARD_DIALOG_RULES };
export type {
  WizardCancelAction,
  WizardDialogProps,
  WizardLayout,
  WizardSecondaryAction,
  WizardStep,
};
