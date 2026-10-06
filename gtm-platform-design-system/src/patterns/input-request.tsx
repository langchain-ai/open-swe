"use client";

/*
 * Rules for InputRequest.
 *
 * The Agent supplies a bounded semantic request. The host owns identity,
 * lifecycle and presentation selection. Surface adapters own pixels.
 */

import {
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type FormEvent,
  type ReactNode,
} from "react";

import { FormSection } from "./form-field";
import { Badge } from "../ui/badge";
import { Box, Inline, Stack } from "../ui/box";
import { Button } from "../ui/button";
import { cn } from "../ui/cn";
import {
  CheckboxGroup,
  CheckboxGroupItem,
} from "../ui/checkbox-group";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxGroup,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
  ComboboxTrigger,
} from "../ui/combobox";
import {
  Collapsible,
  CollapsibleChevron,
  CollapsibleContent,
  CollapsibleTrigger,
} from "../ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "../ui/dialog";
import {
  AlertTriangle,
  CheckCircle,
  ChevronRight,
  File as FileGlyph,
  ListChecks,
  QuestionMarkCircle,
  Upload,
  X,
} from "../ui/glyphs";
import { Icon } from "../ui/icon";
import { HELP_CLASS, LABEL_CLASS } from "../ui/label";
import { Progress } from "../ui/progress";
import { RadioGroup, RadioGroupItem } from "../ui/radio-group";
import { ScrollAreaBody } from "../ui/scroll-area";
import { SearchInput } from "../ui/search-input";
import { Textarea } from "../ui/textarea";

const INPUT_REQUEST_RULES: readonly string[] = [
  "Use InputRequest only to collect missing information. ApprovalArtifact remains the boundary for a consequential send or write.",
  "The Agent supplies semantic sections, stable question IDs, closed question types, labels, help, bounded options and editable prefills. It never supplies React props, component names, layout, HTML, URLs, callbacks, ownership or run tokens.",
  "Every pending web request extends the composer. It never opens a modal or creates a second interaction surface while answers are being collected.",
  "A pending request opens in layout flow so the transcript keeps scrolling, and can collapse to one Needs your input row without losing answers, progress or the blocking state.",
  "The same semantic request maps to native web and Slack controls. Web never renders Block Kit, Slack never translates React, and neither adapter changes the stored question or option IDs.",
  "The composer extension keeps one current question group dominant. Progress is one quiet count and one line in every viewport; a permanent section rail is too much navigation for a temporary prerequisite.",
  "Long option sets become searchable inside the field. Search filters labels only and never changes stable option IDs. Option labels wrap in full and never truncate, and the list scrolls with the step body rather than as a second nested region. Multi-select limits stay visible and are enforced before submission; at the limit the field says no more can be selected instead of silently disabling the rest.",
  "Prefills remain editable and show bounded provenance. They are suggestions from a named source, never hidden defaults.",
  "Validation appears under the field on Continue or the effect-named final action. It is words plus a glyph plus ARIA wiring, never colour alone and never a toast.",
  "Continue validates and saves the current step before advancing. Cancel stays visible as a secondary action. The final action names what the Agent will do next, such as Generate POV execution doc. Help sits between the question label and its control, never after it.",
  "Ask a question saves the draft, sets the form aside as one compact pending row with a Resume action, and returns the chat prompt. The request stays pending with its answers and current section; a question is an ordinary chat turn, never a cancel or a second request.",
  "A submitted request uses InputRequestSubmission inside the existing UserTurn bubble after the continuation message supplies its transcript anchor. Its form icon, title, Submitted status, sections, question labels and answer values stay visibly structured and read-only. Completed forms use tight spacing and two columns for short fields when their own container is wide enough; long text and uploads span both columns. The bubble owns containment and copy; never nest another card or render filled answers as editable inputs. Cancelled and expired requests close without leaving an unanchored card at the bottom of the chat.",
  "Submitted uploads render as file pills when the host supplies an authenticated file destination. The host builds that destination from the owned thread and filename; the request never supplies a URL.",
  "Unknown schema versions or question types fail closed. Show a recoverable unsupported receipt and ask the user to start a fresh request instead of dropping fields.",
];

const MAX_INPUT_REQUEST_QUESTIONS = 12;
const MAX_INPUT_REQUEST_SECTIONS = 10;
const MAX_INPUT_REQUEST_OPTIONS = 160;
const MAX_SHORT_TEXT_CHARS = 500;
const MAX_LONG_TEXT_CHARS = 3_000;
const SEARCHABLE_OPTION_COUNT = 8;

type InputQuestionType =
  | "SHORT_TEXT"
  | "LONG_TEXT"
  | "SINGLE_SELECT"
  | "MULTI_SELECT"
  | "CONFIRM"
  | "FILE_UPLOAD";

type InputRequestState = "PENDING" | "SUBMITTED" | "CANCELLED" | "EXPIRED";
type InputRequestReceiptState = Exclude<InputRequestState, "PENDING"> | "UNSUPPORTED";

type InputRequestPresentation =
  | "COMPOSER_STEP"
  | "UNSUPPORTED";

type InputRequestAnswer = string | readonly string[] | boolean;
type InputRequestAnswers = Readonly<Record<string, InputRequestAnswer | undefined>>;
type InputRequestErrors = Readonly<Record<string, string | undefined>>;

interface InputRequestOption {
  id: string;
  label: string;
  help?: string;
}

interface InputQuestionBase {
  id: string;
  type: InputQuestionType;
  label: string;
  help?: string;
  required?: boolean;
  provenance?: string;
}

interface ShortTextQuestion extends InputQuestionBase {
  type: "SHORT_TEXT";
  placeholder?: string;
}

interface LongTextQuestion extends InputQuestionBase {
  type: "LONG_TEXT";
  placeholder?: string;
}

interface SingleSelectQuestion extends InputQuestionBase {
  type: "SINGLE_SELECT";
  options: readonly InputRequestOption[];
}

interface MultiSelectQuestion extends InputQuestionBase {
  type: "MULTI_SELECT";
  options: readonly InputRequestOption[];
  minSelections?: number;
  maxSelections?: number;
}

interface ConfirmQuestion extends InputQuestionBase {
  type: "CONFIRM";
}

interface FileUploadQuestion extends InputQuestionBase {
  type: "FILE_UPLOAD";
  acceptedFileTypes?: readonly string[];
  maxFiles: number;
}

type InputRequestQuestion =
  | ShortTextQuestion
  | LongTextQuestion
  | SingleSelectQuestion
  | MultiSelectQuestion
  | ConfirmQuestion
  | FileUploadQuestion;

interface InputRequestSection {
  id: string;
  title: string;
  description?: string;
  questions: readonly InputRequestQuestion[];
}

interface InputRequestSpec {
  schemaVersion: number;
  id: string;
  title: string;
  description: string;
  sections: readonly InputRequestSection[];
}

interface InputQuestionFieldProps {
  question: InputRequestQuestion;
  value?: InputRequestAnswer;
  error?: string;
  onValueChange: (value: InputRequestAnswer) => void;
  onUploadFiles?: (files: readonly File[]) => Promise<readonly string[]>;
  onUploadStateChange?: (questionId: string, uploading: boolean) => void;
}

interface InputRequestComposerProps {
  request: InputRequestSpec;
  answers: InputRequestAnswers;
  onAnswerChange: (questionId: string, value: InputRequestAnswer) => void;
  onSubmit: (answers: InputRequestAnswers) => Promise<void>;
  onSaveProgress?: (answers: InputRequestAnswers) => Promise<void>;
  onCancel?: () => void;
  /** Sets the form aside so the person can ask a question; the host saves and hides it. */
  onAskQuestion?: (answers: InputRequestAnswers) => void;
  /** The current section, when the host keeps it across the form being set aside. */
  step?: number;
  onStepChange?: (step: number) => void;
  submitting?: boolean;
  completeLabel?: string;
  displayTitle?: string;
  onUploadFiles?: (files: readonly File[]) => Promise<readonly string[]>;
}

interface InputRequestReceiptProps {
  state: InputRequestReceiptState;
  title?: string;
  summary?: string;
  answeredCount?: number;
  answers?: readonly InputRequestReceiptAnswer[];
  review?: InputRequestReceiptReview;
}

interface InputRequestReceiptAnswer {
  label: string;
  value: string;
}

interface InputRequestReceiptReview {
  request: InputRequestSpec;
  answers: InputRequestAnswers;
}

function inputRequestQuestions(request: InputRequestSpec): readonly InputRequestQuestion[] {
  return request.sections.flatMap((section) => section.questions);
}

function resolveInputRequestPresentation(request: InputRequestSpec): InputRequestPresentation {
  const questions = inputRequestQuestions(request);
  const optionCount = questions.reduce((total, question) => {
    if (question.type !== "SINGLE_SELECT" && question.type !== "MULTI_SELECT") return total;
    return total + question.options.length;
  }, 0);

  if (
    request.schemaVersion !== 1 ||
    request.sections.length === 0 ||
    request.sections.length > MAX_INPUT_REQUEST_SECTIONS ||
    questions.length === 0 ||
    questions.length > MAX_INPUT_REQUEST_QUESTIONS ||
    optionCount > MAX_INPUT_REQUEST_OPTIONS
  ) {
    return "UNSUPPORTED";
  }

  return "COMPOSER_STEP";
}

function answerError(question: InputRequestQuestion, value?: InputRequestAnswer): string | undefined {
  if (question.type === "MULTI_SELECT") {
    if (value === undefined && !question.required) return undefined;
    const selected = Array.isArray(value) ? value : [];
    const minimum = Math.max(question.minSelections ?? 0, question.required === true ? 1 : 0);
    if (selected.length < minimum) {
      return minimum === 1 ? "Choose at least one option." : `Choose at least ${minimum} options.`;
    }
    if (question.maxSelections !== undefined && selected.length > question.maxSelections) {
      return `Choose no more than ${question.maxSelections} options.`;
    }
    return undefined;
  }

  if (question.type === "FILE_UPLOAD") {
    const files = Array.isArray(value) ? value : [];
    if (files.length > question.maxFiles) {
      return `Upload no more than ${question.maxFiles} ${question.maxFiles === 1 ? "file" : "files"}.`;
    }
    if (question.required === true && files.length === 0) {
      return "Upload at least one file.";
    }
    return undefined;
  }

  if (question.type === "SHORT_TEXT" && typeof value === "string" && value.length > MAX_SHORT_TEXT_CHARS) {
    return `Use ${MAX_SHORT_TEXT_CHARS} characters or fewer.`;
  }
  if (question.type === "LONG_TEXT" && typeof value === "string" && value.length > MAX_LONG_TEXT_CHARS) {
    return "Use 3,000 characters or fewer.";
  }
  if (question.required !== true) return undefined;
  if (question.type === "CONFIRM") {
    return typeof value === "boolean" ? undefined : "Choose yes or no.";
  }
  if (typeof value !== "string" || value.trim() === "") {
    return question.type === "SINGLE_SELECT" ? "Choose one option." : "Enter an answer.";
  }
  return undefined;
}

function validateInputRequestSection(
  section: InputRequestSection,
  answers: InputRequestAnswers
): InputRequestErrors {
  return Object.fromEntries(
    section.questions
      .map((question) => [question.id, answerError(question, answers[question.id])] as const)
      .filter((entry) => entry[1] !== undefined)
  );
}

function hasInputRequestErrors(errors: InputRequestErrors): boolean {
  return Object.values(errors).some((error) => error !== undefined);
}

function InputQuestionShell({
  children,
  error,
  help,
  label,
  provenance,
  required,
}: {
  children: (props: {
    describedBy?: string;
    invalid: boolean;
    labelledBy: string;
  }) => ReactNode;
  error?: string;
  help?: string;
  label: string;
  provenance?: string;
  required?: boolean;
}) {
  const labelId = useId();
  const helpId = useId();
  const errorId = useId();
  const invalid = error !== undefined;
  const describedBy = [help === undefined ? null : helpId, invalid ? errorId : null]
    .filter((id): id is string => id !== null)
    .join(" ");

  return (
    <Stack data-slot="input-question-field" data-invalid={invalid} gap="sm">
      <Inline gap="xs" align="center" justify="between">
        <Inline gap="xs" align="center">
          <Box render={<span id={labelId} />} className={LABEL_CLASS}>
            {label}
          </Box>
          {required === true ? (
            <Box render={<span aria-hidden />} className="text-label text-risk">
              *
            </Box>
          ) : null}
        </Inline>
        {provenance === undefined ? null : (
          <Badge
            tier="plain"
            tone="neutral"
            title={`Prefilled from ${provenance}`}
            aria-label={`Prefilled from ${provenance}`}
          >
            Prefilled
          </Badge>
        )}
      </Inline>
      {help === undefined ? null : (
        <Box render={<p id={helpId} />} className={cn(HELP_CLASS, "text-pretty")}>
          {help}
        </Box>
      )}
      {children({
        describedBy: describedBy === "" ? undefined : describedBy,
        invalid,
        labelledBy: labelId,
      })}
      {invalid ? (
        <Inline id={errorId} role="alert" gap="sm" align="start" className="text-label text-risk">
          <Icon icon={AlertTriangle} size="sm" />
          <Box render={<span />}>{error}</Box>
        </Inline>
      ) : null}
    </Stack>
  );
}

function ChoiceRow({ option, selected }: { option: InputRequestOption; selected: boolean }) {
  return (
    <Stack gap="none" className="min-w-0 flex-1">
      <Box render={<span />} className="text-label font-medium text-ink">
        {option.label}
      </Box>
      {option.help === undefined ? null : (
        <Box render={<span />} className="text-meta text-ink-subtle">
          {option.help}
        </Box>
      )}
      <Box render={<span />} className="sr-only">
        {selected ? "Selected" : "Not selected"}
      </Box>
    </Stack>
  );
}

function SingleSelectField({
  error,
  onValueChange,
  question,
  value,
}: InputQuestionFieldProps & { question: SingleSelectQuestion }) {
  const selected = typeof value === "string" ? value : "";
  const selectedOption = question.options.find((option) => option.id === selected);

  return (
    <InputQuestionShell
      error={error}
      help={question.help}
      label={question.label}
      provenance={question.provenance}
      required={question.required}
    >
      {({ describedBy, invalid, labelledBy }) =>
        question.options.length <= 5 ? (
          <RadioGroup
            value={selected}
            aria-labelledby={labelledBy}
            aria-describedby={describedBy}
            aria-invalid={invalid ? true : undefined}
            onValueChange={(next) => onValueChange(next)}
          >
            {question.options.map((option) => (
              <Box
                key={option.id}
                render={<label />}
                className="flex min-h-row-data cursor-pointer items-center gap-2 rounded-control border border-line bg-panel px-3 py-2 hover:bg-hover"
              >
                <RadioGroupItem value={option.id} />
                <ChoiceRow option={option} selected={selected === option.id} />
              </Box>
            ))}
          </RadioGroup>
        ) : (
          <Combobox value={selected} onValueChange={onValueChange}>
            <ComboboxTrigger
              aria-labelledby={labelledBy}
              aria-describedby={describedBy}
              aria-invalid={invalid ? true : undefined}
              placeholder="Choose an option"
            >
              {selectedOption?.label}
            </ComboboxTrigger>
            <ComboboxContent>
              <ComboboxInput placeholder="Search options" />
              <ComboboxList>
                <ComboboxEmpty>No matching options.</ComboboxEmpty>
                <ComboboxGroup>
                  {question.options.map((option) => (
                    <ComboboxItem key={option.id} value={option.id}>
                      {option.label}
                    </ComboboxItem>
                  ))}
                </ComboboxGroup>
              </ComboboxList>
            </ComboboxContent>
          </Combobox>
        )
      }
    </InputQuestionShell>
  );
}

function MultiSelectField({
  error,
  onValueChange,
  question,
  value,
}: InputQuestionFieldProps & { question: MultiSelectQuestion }) {
  const [query, setQuery] = useState("");
  const selected = Array.isArray(value) ? value : [];
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visible = useMemo(
    () =>
      normalizedQuery === ""
        ? question.options
        : question.options.filter((option) => option.label.toLocaleLowerCase().includes(normalizedQuery)),
    [normalizedQuery, question.options]
  );
  const atLimit = question.maxSelections !== undefined && selected.length >= question.maxSelections;

  return (
    <InputQuestionShell
      error={error}
      help={question.help}
      label={question.label}
      provenance={question.provenance}
      required={question.required}
    >
      {({ describedBy, invalid, labelledBy }) => (
        <Stack gap="sm">
          <Inline gap="sm" align="center" justify="between" wrap>
            <Box render={<span />} className="text-meta text-ink-subtle tabular-nums">
              {question.maxSelections === undefined
                ? `${selected.length} selected`
                : `${selected.length} of ${question.maxSelections} selected`}
            </Box>
            {question.options.length < SEARCHABLE_OPTION_COUNT ? null : (
              <Box className="w-full max-w-64">
                <SearchInput
                  value={query}
                  onValueChange={setQuery}
                  label={`Search ${question.label}`}
                  placeholder="Search options"
                />
              </Box>
            )}
          </Inline>
          {/*
           * The list is not its own scroll region. The step body already
           * scrolls, and a capped list inside it made two nested regions:
           * the wheel moved one and the other hid the rest of the options.
           */}
          <CheckboxGroup
            value={[...selected]}
            aria-labelledby={labelledBy}
            aria-describedby={describedBy}
            aria-invalid={invalid ? true : undefined}
            onValueChange={(next) => onValueChange(next)}
          >
            {visible.map((option) => (
              <CheckboxGroupItem
                key={option.id}
                value={option.id}
                disabled={atLimit && !selected.includes(option.id)}
                className="mx-0 px-3"
              >
                {option.label}
              </CheckboxGroupItem>
            ))}
          </CheckboxGroup>
          {atLimit && question.maxSelections !== undefined ? (
            <Box render={<p role="status" />} className="text-meta text-ink-subtle">
              {`No more than ${question.maxSelections} can be selected. Clear one to choose another.`}
            </Box>
          ) : null}
          {visible.length === 0 ? (
            <Box render={<p />} className="text-meta text-ink-subtle">
              No matching options.
            </Box>
          ) : null}
        </Stack>
      )}
    </InputQuestionShell>
  );
}

function ConfirmField({
  error,
  onValueChange,
  question,
  value,
}: InputQuestionFieldProps & { question: ConfirmQuestion }) {
  const selected = typeof value === "boolean" ? (value ? "YES" : "NO") : "";
  return (
    <InputQuestionShell
      error={error}
      help={question.help}
      label={question.label}
      provenance={question.provenance}
      required={question.required}
    >
      {({ describedBy, invalid, labelledBy }) => (
        <RadioGroup
          value={selected}
          aria-labelledby={labelledBy}
          aria-describedby={describedBy}
          aria-invalid={invalid ? true : undefined}
          onValueChange={(next) => onValueChange(next === "YES")}
          className="grid-cols-2"
        >
          {(["YES", "NO"] as const).map((option) => (
            <Box
              key={option}
              render={<label />}
              className="flex h-row-data cursor-pointer items-center gap-2 rounded-control border border-line bg-panel px-3 hover:bg-hover"
            >
              <RadioGroupItem value={option} />
              <Box render={<span />} className="text-label font-medium text-ink">
                {option === "YES" ? "Yes" : "No"}
              </Box>
            </Box>
          ))}
        </RadioGroup>
      )}
    </InputQuestionShell>
  );
}

function FileUploadField({
  error,
  onUploadFiles,
  onUploadStateChange,
  onValueChange,
  question,
  value,
}: InputQuestionFieldProps & { question: FileUploadQuestion }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string>();
  const selected = Array.isArray(value) ? value : [];
  const remaining = Math.max(0, question.maxFiles - selected.length);
  const accept = question.acceptedFileTypes?.map((extension) => `.${extension}`).join(",");

  async function addFiles(files: readonly File[]): Promise<void> {
    if (files.length === 0 || onUploadFiles === undefined) return;
    if (files.length > remaining) {
      setUploadError(
        `Upload no more than ${question.maxFiles} ${question.maxFiles === 1 ? "file" : "files"}.`
      );
      return;
    }
    setUploadError(undefined);
    setUploading(true);
    onUploadStateChange?.(question.id, true);
    try {
      const names = await onUploadFiles(files);
      onValueChange([...selected, ...names]);
    } catch {
      setUploadError("Files could not be uploaded. Try again.");
    } finally {
      setUploading(false);
      onUploadStateChange?.(question.id, false);
    }
  }

  return (
    <InputQuestionShell
      error={uploadError ?? error}
      help={question.help}
      label={question.label}
      provenance={question.provenance}
      required={question.required}
    >
      {({ describedBy, invalid, labelledBy }) => (
        <Stack gap="sm">
          <input
            ref={inputRef}
            type="file"
            multiple={question.maxFiles > 1}
            accept={accept}
            className="hidden"
            aria-labelledby={labelledBy}
            aria-describedby={describedBy}
            aria-invalid={invalid ? true : undefined}
            onChange={(event) => {
              const files = Array.from(event.target.files ?? []);
              event.target.value = "";
              void addFiles(files);
            }}
          />
          {selected.length === 0 ? null : (
            <Stack gap="xs">
              {selected.map((name) => (
                <Inline
                  key={name}
                  align="center"
                  gap="sm"
                  className="min-h-row-data rounded-control border border-line bg-panel px-3 py-2"
                >
                  <Icon icon={FileGlyph} size="sm" className="shrink-0 text-ink-subtle" />
                  <Box render={<span title={name} />} className="min-w-0 flex-1 truncate text-label text-ink">
                    {name}
                  </Box>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Remove ${name}`}
                    disabled={uploading}
                    onClick={() => onValueChange(selected.filter((item) => item !== name))}
                  >
                    <Icon icon={X} size="sm" />
                  </Button>
                </Inline>
              ))}
            </Stack>
          )}
          <Inline gap="sm" align="center">
            <Button
              type="button"
              variant="outline"
              size="compact"
              loading={uploading}
              disabled={onUploadFiles === undefined || remaining === 0}
              onClick={() => inputRef.current?.click()}
            >
              <Icon icon={Upload} size="sm" />
              {uploading ? "Uploading" : selected.length === 0 ? "Upload files" : "Add files"}
            </Button>
            <Box render={<span aria-live="polite" />} className="text-meta text-ink-subtle tabular-nums">
              {uploading
                ? "Uploading to the Agent workspace…"
                : `${selected.length} of ${question.maxFiles}`}
            </Box>
          </Inline>
        </Stack>
      )}
    </InputQuestionShell>
  );
}

/*
 * A text answer that wraps and grows to fit what it holds, so a rep reads a
 * prefilled answer whole instead of scrolling a single line sideways. A short
 * answer starts at one line and keeps its single-line contract: a newline is
 * never inserted, so Enter cannot add one and a pasted one becomes a space.
 */
function WrappingTextarea({
  onValueChange,
  rows,
  singleLine = false,
  value,
  ...props
}: Omit<ComponentProps<"textarea">, "onChange" | "value"> & {
  onValueChange: (value: string) => void;
  singleLine?: boolean;
  value: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight}px`;
  }, [value]);
  return (
    <Textarea
      {...props}
      ref={ref}
      value={value}
      rows={singleLine ? 1 : rows}
      className={cn("overflow-hidden", singleLine && "min-h-control resize-none")}
      onKeyDown={(event) => {
        if (singleLine && event.key === "Enter") event.preventDefault();
      }}
      onChange={(event) =>
        onValueChange(singleLine ? event.target.value.replace(/[\r\n]+/g, " ") : event.target.value)
      }
    />
  );
}

function InputQuestionField({
  question,
  value,
  error,
  onValueChange,
  onUploadFiles,
  onUploadStateChange,
}: InputQuestionFieldProps) {
  switch (question.type) {
    case "SHORT_TEXT":
      return (
        <InputQuestionShell
          label={question.label}
          help={question.help}
          error={error}
          provenance={question.provenance}
          required={question.required}
        >
          {({ describedBy, invalid, labelledBy }) => (
            <WrappingTextarea
              value={typeof value === "string" ? value : ""}
              placeholder={question.placeholder}
              singleLine
              aria-labelledby={labelledBy}
              aria-describedby={describedBy}
              aria-invalid={invalid ? true : undefined}
              aria-required={question.required === true ? true : undefined}
              maxLength={MAX_SHORT_TEXT_CHARS}
              onValueChange={onValueChange}
            />
          )}
        </InputQuestionShell>
      );
    case "LONG_TEXT":
      return (
        <InputQuestionShell
          label={question.label}
          help={question.help}
          error={error}
          provenance={question.provenance}
          required={question.required}
        >
          {({ describedBy, invalid, labelledBy }) => (
            <WrappingTextarea
              value={typeof value === "string" ? value : ""}
              placeholder={question.placeholder}
              rows={5}
              aria-labelledby={labelledBy}
              aria-describedby={describedBy}
              aria-invalid={invalid ? true : undefined}
              aria-required={question.required === true ? true : undefined}
              maxLength={MAX_LONG_TEXT_CHARS}
              onValueChange={onValueChange}
            />
          )}
        </InputQuestionShell>
      );
    case "SINGLE_SELECT":
      return (
        <SingleSelectField
          question={question}
          value={value}
          error={error}
          onValueChange={onValueChange}
        />
      );
    case "MULTI_SELECT":
      return (
        <MultiSelectField
          question={question}
          value={value}
          error={error}
          onValueChange={onValueChange}
        />
      );
    case "CONFIRM":
      return (
        <ConfirmField
          question={question}
          value={value}
          error={error}
          onValueChange={onValueChange}
        />
      );
    case "FILE_UPLOAD":
      return (
        <FileUploadField
          question={question}
          value={value}
          error={error}
          onValueChange={onValueChange}
          onUploadFiles={onUploadFiles}
          onUploadStateChange={onUploadStateChange}
        />
      );
  }
}

function InputRequestSectionFields({
  answers,
  errors,
  onAnswerChange,
  onUploadFiles,
  onUploadStateChange,
  section,
  showIntro = true,
}: {
  answers: InputRequestAnswers;
  errors?: InputRequestErrors;
  onAnswerChange: (questionId: string, value: InputRequestAnswer) => void;
  onUploadFiles?: (files: readonly File[]) => Promise<readonly string[]>;
  onUploadStateChange?: (questionId: string, uploading: boolean) => void;
  section: InputRequestSection;
  showIntro?: boolean;
}) {
  const fields = section.questions.map((question) => (
    <InputQuestionField
      key={question.id}
      question={question}
      value={answers[question.id]}
      error={errors?.[question.id]}
      onValueChange={(value) => onAnswerChange(question.id, value)}
      onUploadFiles={onUploadFiles}
      onUploadStateChange={onUploadStateChange}
    />
  ));

  if (!showIntro) {
    return <Stack gap="lg">{fields}</Stack>;
  }

  return (
    <FormSection title={section.title} description={section.description}>
      {fields}
    </FormSection>
  );
}

function InputRequestComposer({
  answers,
  completeLabel = "Continue",
  displayTitle,
  onAnswerChange,
  onAskQuestion,
  onCancel,
  onSaveProgress,
  onStepChange,
  onSubmit,
  onUploadFiles,
  request,
  step: controlledStep,
  submitting = false,
}: InputRequestComposerProps) {
  const [internalStep, setInternalStep] = useState(0);
  const step = controlledStep ?? internalStep;
  function setStep(next: number | ((current: number) => number)): void {
    const resolved = typeof next === "function" ? next(step) : next;
    if (controlledStep === undefined) setInternalStep(resolved);
    onStepChange?.(resolved);
  }
  const [errors, setErrors] = useState<InputRequestErrors>({});
  const [uploadingQuestions, setUploadingQuestions] = useState<ReadonlySet<string>>(new Set());
  const submittingRef = useRef(false);
  const presentation = resolveInputRequestPresentation(request);
  const section = request.sections[step];
  const uploadsInFlight = uploadingQuestions.size > 0;

  function setQuestionUploading(questionId: string, uploading: boolean): void {
    setUploadingQuestions((current) => {
      const next = new Set(current);
      if (uploading) next.add(questionId);
      else next.delete(questionId);
      return next;
    });
  }

  if (section === undefined || presentation === "UNSUPPORTED") {
    return <InputRequestReceipt state="UNSUPPORTED" />;
  }

  function validateStep(index: number): boolean {
    const target = request.sections[index];
    if (target === undefined) return false;
    if (index === request.sections.length - 1) {
      const sectionErrors = request.sections.map((item) =>
        validateInputRequestSection(item, answers)
      );
      const nextErrors = Object.assign({}, ...sectionErrors) as InputRequestErrors;
      const firstInvalid = sectionErrors.findIndex(hasInputRequestErrors);
      setErrors(nextErrors);
      if (firstInvalid >= 0) {
        setStep(firstInvalid);
        return false;
      }
      return true;
    }
    const nextErrors = validateInputRequestSection(target, answers);
    setErrors(nextErrors);
    return !hasInputRequestErrors(nextErrors);
  }

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (submitting || uploadsInFlight || submittingRef.current || !validateStep(step)) return;
    submittingRef.current = true;
    const finalStep = step === request.sections.length - 1;
    const action = finalStep
      ? onSubmit(answers)
      : (onSaveProgress?.(answers) ?? Promise.resolve()).then(() => {
          setStep((current) => current + 1);
          setErrors({});
        });
    void action
      .catch(() => undefined)
      .finally(() => {
        submittingRef.current = false;
      });
  }

  return (
    <Stack
      render={<form noValidate onSubmit={submit} />}
      data-slot="input-request-composer"
      aria-label={displayTitle ?? request.title}
      aria-busy={submitting || uploadsInFlight}
      gap="none"
      className="w-full overflow-hidden rounded-shell border border-line bg-composer-card"
    >
      <Collapsible defaultOpen>
        <CollapsibleTrigger
          type="button"
          aria-label={`Toggle ${displayTitle ?? request.title} details`}
          className="w-full rounded-none p-3 hover:bg-hover focus-visible:ring-inset"
        >
          <Inline gap="sm" align="center" className="w-full">
            <Icon icon={QuestionMarkCircle} size="md" className="shrink-0 text-ink-muted" />
            <Box render={<span />} className="min-w-0 flex-1 truncate text-left text-label font-semibold text-ink">
              {displayTitle ?? request.title}
            </Box>
            <Badge tier="quiet" tone="info" dot>
              Needs your input
            </Badge>
            <Box render={<span />} className="shrink-0 text-meta text-ink-subtle tabular-nums">
              {`${step + 1} of ${request.sections.length}`}
            </Box>
            <CollapsibleChevron />
          </Inline>
        </CollapsibleTrigger>

        <CollapsibleContent>
          {request.sections.length === 1 ? null : (
            <Box className="px-3 pb-3">
              <Progress
                label="Answer progress"
                value={step + 1}
                max={request.sections.length}
              />
            </Box>
          )}

          <ScrollAreaBody overflow="vertical" className="max-h-96 border-y border-line">
            {/*
             * `min-w-0` is load-bearing: a fieldset defaults to a min-content
             * inline size, so one long option label widened the whole step
             * past the composer and every label ran off its edge.
             */}
            <fieldset disabled={submitting || uploadsInFlight} className="min-w-0">
            <Stack
              role="region"
              aria-label={`${section.title}, ${step + 1} of ${request.sections.length}`}
              gap="lg"
              className="p-3"
            >
              <Box render={<p aria-live="polite" />} className="sr-only">
                {`${section.title}, ${step + 1} of ${request.sections.length}`}
              </Box>
              <InputRequestSectionFields
                section={section}
                answers={answers}
                errors={errors}
                onAnswerChange={onAnswerChange}
                onUploadFiles={onUploadFiles}
                onUploadStateChange={setQuestionUploading}
                showIntro={false}
              />
            </Stack>
            </fieldset>
          </ScrollAreaBody>

          <Inline gap="sm" align="center" justify="between" wrap className="p-2">
            <Inline gap="sm" align="center" wrap>
              {onCancel === undefined ? null : (
                <Button
                  type="button"
                  variant="ghost"
                  size="compact"
                  disabled={submitting || uploadsInFlight}
                  onClick={onCancel}
                >
                  Cancel
                </Button>
              )}
              {onAskQuestion === undefined ? null : (
                <Button
                  type="button"
                  variant="ghost"
                  size="compact"
                  disabled={submitting || uploadsInFlight}
                  onClick={() => onAskQuestion(answers)}
                >
                  Ask a question
                </Button>
              )}
              {step === 0 ? null : (
                <Button
                  type="button"
                  variant="ghost"
                  disabled={submitting || uploadsInFlight}
                  onClick={() => {
                    setStep((current) => current - 1);
                    setErrors({});
                    if (onSaveProgress !== undefined) {
                      void onSaveProgress(answers).catch(() => undefined);
                    }
                  }}
                >
                  Back
                </Button>
              )}
            </Inline>
            <Button type="submit" loading={submitting} disabled={uploadsInFlight} className="ml-auto">
              {step === request.sections.length - 1 ? completeLabel : "Continue"}
            </Button>
          </Inline>
        </CollapsibleContent>
      </Collapsible>
    </Stack>
  );
}

/*
 * The form set aside so the rep can ask about it. One row, above the prompt
 * the rep is typing, that says the request is still waiting and where they
 * were in it. Resume brings the same form back at the same section.
 */
function InputRequestPausedRow({
  onResume,
  sectionCount,
  step,
  title,
}: {
  onResume: () => void;
  sectionCount: number;
  step: number;
  title: string;
}) {
  return (
    <Inline
      data-slot="input-request-paused"
      gap="sm"
      align="center"
      className="w-full border-b border-line pb-2"
    >
      <Icon icon={QuestionMarkCircle} size="md" className="shrink-0 text-ink-muted" />
      <Stack gap="none" className="min-w-0 flex-1">
        <Box render={<span />} className="truncate text-label font-medium text-ink">
          {title}
        </Box>
        <Box render={<span />} className="truncate text-meta text-ink-subtle">
          {`Answers saved, ${step + 1} of ${sectionCount}. Ask your question below.`}
        </Box>
      </Stack>
      <Badge tier="quiet" tone="info" dot className="hidden sm:inline-flex">
        Needs your input
      </Badge>
      <Button type="button" variant="outline" size="compact" onClick={onResume}>
        Resume form
      </Button>
    </Inline>
  );
}

const RECEIPT_COPY: Record<InputRequestReceiptState, { label: string; summary?: string }> = {
  SUBMITTED: {
    label: "Submitted",
  },
  CANCELLED: {
    label: "Request cancelled",
    summary: "No answers were submitted.",
  },
  EXPIRED: {
    label: "Request expired",
    summary: "Ask the Agent to start it again.",
  },
  UNSUPPORTED: {
    label: "Form version unsupported",
    summary: "This client cannot safely render every field. Ask the Agent to start a compatible request.",
  },
};

function formatInputRequestAnswer(
  question: InputRequestQuestion,
  value: InputRequestAnswer | undefined
): string {
  if (
    value === undefined ||
    (typeof value === "string" && value.trim().length === 0) ||
    (Array.isArray(value) && value.length === 0)
  ) {
    return "Not answered";
  }

  switch (question.type) {
    case "SINGLE_SELECT":
      return typeof value === "string"
        ? question.options.find((option) => option.id === value)?.label ?? value
        : String(value);
    case "MULTI_SELECT":
      return Array.isArray(value)
        ? value
            .map(
              (selectedId) =>
                question.options.find((option) => option.id === selectedId)?.label ??
                selectedId
            )
            .join(", ")
        : String(value);
    case "CONFIRM":
      return value === true ? "Yes" : "No";
    case "FILE_UPLOAD":
      return Array.isArray(value) ? value.join(", ") : String(value);
    case "SHORT_TEXT":
    case "LONG_TEXT":
      return String(value);
  }
}

function InputRequestReview({
  answers,
  request,
  compact = false,
  fileHref,
}: InputRequestReceiptReview & { compact?: boolean; fileHref?: (name: string) => string }) {
  const showSectionTitles = request.sections.length > 1;

  return (
    <Stack data-slot="input-request-review" gap={compact ? "lg" : "2xl"}>
      {request.sections.map((section) => (
        <Stack key={section.id} gap={compact ? "sm" : "lg"}>
          {showSectionTitles ? (
            <Box render={<h3 />} className={compact ? "text-meta font-medium text-ink-muted" : LABEL_CLASS}>
              {section.title}
            </Box>
          ) : null}
          <Box render={<dl />} className={compact ? "grid grid-cols-1 gap-x-5 gap-y-3 @sm:grid-cols-2" : "flex flex-col gap-4"}>
            {section.questions.map((question) => {
              const answer = answers[question.id];
              const value = formatInputRequestAnswer(question, answer);
              const unanswered = value === "Not answered";
              return (
                <Stack
                  key={question.id}
                  render={<div />}
                  gap={compact ? "none" : "xs"}
                  className={cn(
                    "min-w-0",
                    compact && (question.type === "LONG_TEXT" || question.type === "FILE_UPLOAD") && "col-span-full"
                  )}
                >
                  <Box render={<dt />} className="text-meta text-ink-subtle">
                    {question.label}
                  </Box>
                  <Box
                    render={<dd />}
                    className={cn(
                      "m-0 whitespace-pre-wrap wrap-anywhere text-label",
                      unanswered ? "text-ink-subtle" : "text-ink"
                    )}
                  >
                    {question.type === "FILE_UPLOAD" && fileHref && Array.isArray(answer) && !unanswered ? (
                      <Inline gap="sm" wrap className="pt-1">
                        {answer.map((name) => (
                          <Button
                            key={name}
                            variant="outline"
                            size="compact"
                            className="max-w-full rounded-full"
                            nativeButton={false}
                            role="link"
                            render={<a href={fileHref(name)} target="_blank" rel="noopener noreferrer" />}
                          >
                            <Icon icon={FileGlyph} size="sm" />
                            <Box render={<span />} className="truncate">{name}</Box>
                          </Button>
                        ))}
                      </Inline>
                    ) : value}
                  </Box>
                </Stack>
              );
            })}
          </Box>
        </Stack>
      ))}
    </Stack>
  );
}

/** A completed form's contents. The containing user turn owns the bubble. */
function InputRequestSubmission({
  answers,
  request,
  title = request.title,
  fileHref,
}: InputRequestReceiptReview & { title?: string; fileHref?: (name: string) => string }) {
  return (
    <Stack data-slot="input-request-submission" gap="md" className="@container w-lg max-w-full min-w-0">
      <Inline align="start" gap="sm" className="border-b border-line pb-2">
        <Icon icon={ListChecks} size="md" className="shrink-0 text-ink-muted" />
        <Box render={<p />} className="min-w-0 flex-1 wrap-anywhere text-label font-medium text-ink">
          {title}
        </Box>
        <Badge tier="quiet" tone="positive">Submitted</Badge>
      </Inline>
      <InputRequestReview request={request} answers={answers} compact fileHref={fileHref} />
    </Stack>
  );
}

function InputRequestReceipt({
  answeredCount,
  answers,
  review,
  state,
  summary,
  title,
}: InputRequestReceiptProps) {
  const copy = RECEIPT_COPY[state];
  const tone = state === "SUBMITTED" ? "positive" : state === "EXPIRED" ? "attention" : "neutral";
  const answerSummary = answers
    ?.slice(0, 3)
    .map((answer) => `${answer.label}: ${answer.value}`)
    .join(" · ");
  const hiddenAnswerCount = Math.max(0, (answers?.length ?? 0) - 3);
  const detail =
    summary ??
    (answerSummary === undefined || answerSummary.length === 0
      ? answeredCount === undefined
        ? copy.summary
        : `${answeredCount} ${answeredCount === 1 ? "answer" : "answers"}`
      : `${answerSummary}${hiddenAnswerCount > 0 ? ` · +${hiddenAnswerCount} more` : ""}`);
  const icon = state === "SUBMITTED" ? CheckCircle : QuestionMarkCircle;
  const canReview = state === "SUBMITTED" && review !== undefined;
  const contents = (
    <>
      <Icon
        icon={icon}
        size="sm"
        className={state === "SUBMITTED" ? "text-positive" : "text-ink-muted"}
      />
      <Box className="min-w-0 flex-1">
        <Box render={<p />} className="truncate text-label font-medium text-ink">
          {title ?? copy.label}
        </Box>
        {detail === undefined ? null : (
          <Box
            render={<p title={detail} />}
            className="truncate text-meta text-ink-subtle"
          >
            {detail}
          </Box>
        )}
      </Box>
      <Badge tier="quiet" tone={tone}>
        {state === "SUBMITTED" ? "Submitted" : state === "CANCELLED" ? "Cancelled" : state === "EXPIRED" ? "Expired" : "Unsupported"}
      </Badge>
      {canReview ? (
        <Icon icon={ChevronRight} size="sm" className="text-ink-subtle" />
      ) : null}
    </>
  );

  if (!canReview) {
    return (
      <Inline
        data-slot="input-request-receipt"
        align="center"
        gap="sm"
        className="w-full rounded-panel border border-line bg-panel px-3 py-2"
      >
        {contents}
      </Inline>
    );
  }

  const reviewTitle = title ?? review.request.title;
  return (
    <Dialog>
      <DialogTrigger
        render={
          <button
            type="button"
            data-slot="input-request-receipt"
            className="flex w-full cursor-pointer flex-row items-center justify-start gap-2 rounded-panel border border-line bg-panel px-3 py-2 text-left outline-none transition-colors duration-fast ease-out-quint hover:bg-hover focus-visible:ring-2 focus-visible:ring-primary motion-reduce:transition-none"
          />
        }
      >
        {contents}
      </DialogTrigger>
      <DialogContent className="flex max-h-dvh min-h-0 flex-col gap-0 overflow-hidden p-0 sm:max-w-xl">
        <DialogHeader className="shrink-0 px-4 py-3">
          <DialogTitle>{reviewTitle}</DialogTitle>
          <DialogDescription className="sr-only">
            Review the answers submitted to the Agent.
          </DialogDescription>
        </DialogHeader>
        <Box className="min-h-0 flex-1 border-t border-line">
          <ScrollAreaBody overflow="vertical">
            <Box padding="md">
              <InputRequestReview request={review.request} answers={review.answers} />
            </Box>
          </ScrollAreaBody>
        </Box>
      </DialogContent>
    </Dialog>
  );
}

export {
  INPUT_REQUEST_RULES,
  InputQuestionField,
  InputRequestComposer,
  InputRequestPausedRow,
  InputRequestReceipt,
  InputRequestSubmission,
  InputRequestSectionFields,
  formatInputRequestAnswer,
  hasInputRequestErrors,
  resolveInputRequestPresentation,
  validateInputRequestSection,
};
export type {
  ConfirmQuestion,
  FileUploadQuestion,
  InputQuestionType,
  InputRequestAnswer,
  InputRequestReceiptAnswer,
  InputRequestAnswers,
  InputRequestErrors,
  InputRequestOption,
  InputRequestPresentation,
  InputRequestQuestion,
  InputRequestReceiptState,
  InputRequestSection,
  InputRequestSpec,
  InputRequestState,
  LongTextQuestion,
  MultiSelectQuestion,
  ShortTextQuestion,
  SingleSelectQuestion,
};
