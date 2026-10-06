"use client";

/*
 * Rules for FormField / FormSection.
 *
 * The rules themselves are `FORM_RULES` below, not this comment. `/design`
 * renders that array verbatim beside the live pattern, so the anatomy a builder
 * reads in the gallery and the anatomy this file imposes are the same strings.
 *
 * This is the explicit submit family: creation flows, invite flows, wizard
 * steps: surfaces where nothing has happened until the user presses the one
 * button that makes it happen. It is deliberately NOT the settings family, which
 * saves on change and has no button at all; see `setting-section.tsx`. Choosing
 * between the two is the first decision a builder makes here, so it is rule one.
 *
 * What the code enforces: the label points at the control, the help line and the
 * error line are wired into `aria-describedby`, and an error puts the control on
 * `aria-invalid`, all of it by cloning the control rather than trusting a call
 * site to remember. What the code enforces by omission: FormSection has no
 * actions slot, because the form has one primary action and a per section slot
 * is how a form grows three.
 */

import { cloneElement, useId } from "react";
import type { ReactElement, ReactNode } from "react";

import { Box, Inline, Stack } from "../ui/box";
import { AlertTriangle } from "../ui/glyphs";
import { Icon } from "../ui/icon";
import { HELP_CLASS, LABEL_CLASS } from "../ui/label";

const FORM_RULES: readonly string[] = [
  "This is the explicit submit family: creation flows, invite flows, wizard steps. Nothing has happened until the user presses the button. If the surface is configuration, a set of independent switches a user comes back to and adjusts, it is not a form, it is SettingSection, and it saves on change with no button at all. Pick the family first; the two are not interchangeable fills of one pattern.",
  "VALIDATION DISPLAY, ADOPTED DEFAULT, PENDING AMAL'S CONFIRMATION: a validation message renders inline, under the control that is wrong, on blur or on submit. Never on every keystroke, because a field the user has not finished typing is not yet wrong; and never as a toast, because a toast puts the complaint somewhere other than the thing being complained about, and it expires while the field does not.",
  "The error line lives under the CONTROL, not under the label and not at the top of the form. It keeps the message aligned to the thing that is wrong, and it keeps the label from reflowing when the message appears.",
  "An error is never only a colour. The line carries a glyph and words on the risk role, the control goes aria-invalid, and aria-describedby points at the message, so the failure survives a screen reader and a monochrome screen alike. This pattern wires all three itself: a call site cannot forget.",
  "Help text is the field's permanent explanation and the error is a temporary complaint, so both can be on screen at once and both are described to the control. Do not use the help line as an error slot that changes colour.",
  "A required field is marked once, beside the label, and the marker is decorative: the control carries aria-required and nothing else. Native browser validation is off by design, because its bubbles are a second validation display and the rule above says there is one.",
  "One primary action per form, named for its effect. 'Save policy', 'Send invites', 'Create campaign'. Never 'Submit', never 'OK'. The name is the last thing a user reads before committing and it should say what will happen.",
  "That one action belongs to the FORM, not to a section, which is why FormSection has no actions slot. A form with an action row per section is a form that will grow three primary buttons, and the user will not know which one finishes the job.",
  "FormSection groups fields at ONE gap under one heading. The heading and its description are one pair at `xs`. That pair sits one `md` step above the first field. A label sits one `sm` step above its control. Sibling sections stack at one `2xl` step, only through FormStack or SECTION_STACK_GAP. A wizard pane uses that same gap. Do not pass a local gap around sections. Native fieldset and legend chrome is reset so the title is the same size as a field label, not a leftover heading. No nested groups, no per field spacing overrides: an even rhythm is what makes a long form scannable, and a section that needs its own rhythm is a second FormSection, not a denser group.",
  "A form spends two sizes: text-label and text-meta. The section title and the field label share LABEL_CLASS from ui/label. The title text lives in a span inside the legend, because a native legend ignores font on the element itself. Description and help share HELP_CLASS. An error stays text-label and shifts to the risk role; state is colour, not a third size. Typed values use FIELD_VALUE_CLASS from Input: 16px below md so Safari does not zoom, then the label size. text-page belongs to PageFrame, not to a dialog or a fieldset. ReUI form-2/4/7 and the settings blocks stay in two rungs the same way.",
  "No tabbed forms, rejected from the ReUI survey. Seven tabs over one form with one footer means a user can leave an unsaved, invalid field behind a tab they cannot see. A form that is too long for a column is a wizard with steps, which is honest about the same thing.",
  "Destructive submits still compose ConfirmableAction. A primary button named for its effect explains the effect; it does not authorise an irreversible one.",
];

/** The one gap between a label and its control. */
const FIELD_LABEL_GAP = "sm" as const;
/** Offset under the legend when a description follows. Pair at xs. */
const SECTION_INTRO_OFFSET = "mt-1";
/** Offset under the legend when fields follow with no description. Pair at md. */
const SECTION_HEADING_OFFSET = "mt-3";
/** The one gap between the heading pair and the first field. */
const SECTION_HEADING_GAP = "md" as const;
/** The one gap between fields in a section. Nothing overrides it per field. */
const FIELD_STACK_GAP = "lg" as const;
/** The one gap between sibling sections. Wizard panes use this too. */
const SECTION_STACK_GAP = "2xl" as const;

const SECTION_CLASS = "min-w-0 m-0";
const SECTION_LEGEND_CLASS = "float-none w-full";

interface FormFieldProps {
  label: string;
  /**
   * The control. Cloned with the field's id and its aria wiring, so the
   * association cannot be forgotten or half done at a call site.
   */
  control: ReactElement<Record<string, unknown>>;
  /** The field's permanent explanation, under the control. */
  help?: string;
  /**
   * The validation message. Present means invalid: the control goes
   * aria-invalid and points at this line. Set it on blur or on submit.
   */
  error?: string;
  /** Marks the label and sets aria-required. Native validation stays off. */
  required?: boolean;
}

/** One labelled control with its help line and its inline validation message. */
function FormField({ label, control, help, error, required }: FormFieldProps) {
  const fieldId = useId();
  const helpId = useId();
  const errorId = useId();

  const invalid = error !== undefined;
  const described = [
    help === undefined ? null : helpId,
    invalid ? errorId : null,
  ].filter((id): id is string => id !== null);

  const wired = cloneElement(control, {
    "aria-describedby":
      described.length === 0 ? undefined : described.join(" "),
    "aria-invalid": invalid ? true : undefined,
    "aria-required": required === true ? true : undefined,
    id: fieldId,
  });

  return (
    <Stack data-slot="form-field" data-invalid={invalid} gap={FIELD_LABEL_GAP}>
      <Inline gap="xs" align="center">
        <Box
          render={<label htmlFor={fieldId} />}
          className={LABEL_CLASS}
        >
          {label}
        </Box>
        {required === true ? (
          <Box render={<span aria-hidden />} className="text-label text-risk">
            *
          </Box>
        ) : null}
      </Inline>

      {wired}

      {help === undefined ? null : (
        <Box render={<p id={helpId} />} className={HELP_CLASS}>
          {help}
        </Box>
      )}

      {invalid ? (
        <Inline
          id={errorId}
          role="alert"
          gap="sm"
          align="start"
          className="text-label text-risk"
        >
          <Icon icon={AlertTriangle} size="sm" />
          <Box render={<span />}>{error}</Box>
        </Inline>
      ) : null}
    </Stack>
  );
}

interface FormSectionProps {
  title: string;
  /** Optional id on the title span, so a control inside can point at it. */
  titleId?: string;
  /** One or two lines of context for the group, above the first field. */
  description?: string;
  /** Optional id on the description, so a control inside can point at it. */
  descriptionId?: string;
  /** FormField children, at the one gap. */
  children: ReactNode;
}

/**
 * A group of fields under one heading, at one gap. No actions slot: the form's
 * single primary action belongs to the form, not to a section.
 */
function FormSection({
  children,
  description,
  descriptionId,
  title,
  titleId,
}: FormSectionProps) {
  return (
    <Box
      render={<fieldset />}
      data-slot="form-section"
      padding="none"
      border="none"
      className={SECTION_CLASS}
    >
      <Box
        render={<legend />}
        padding="none"
        className={SECTION_LEGEND_CLASS}
      >
        <Box render={<span id={titleId} />} className={LABEL_CLASS}>
          {title}
        </Box>
      </Box>
      <Stack
        gap={SECTION_HEADING_GAP}
        className={
          description === undefined
            ? SECTION_HEADING_OFFSET
            : SECTION_INTRO_OFFSET
        }
      >
        {description === undefined ? null : (
          <Box render={<p id={descriptionId} />} className={HELP_CLASS}>
            {description}
          </Box>
        )}
        <Stack data-slot="form-fields" gap={FIELD_STACK_GAP}>
          {children}
        </Stack>
      </Stack>
    </Box>
  );
}

/**
 * The only stack for sibling FormSections. The gap is locked. A wizard pane
 * uses the same step so a branch cannot invent a tighter form.
 */
function FormStack({
  children,
  className,
  render,
}: {
  children: ReactNode;
  className?: string;
  render?: ReactElement;
}) {
  return (
    <Stack
      data-slot="form-stack"
      gap={SECTION_STACK_GAP}
      className={className}
      render={render}
    >
      {children}
    </Stack>
  );
}

export { FormField, FormSection, FormStack, FORM_RULES, SECTION_STACK_GAP };
export type { FormFieldProps, FormSectionProps };
