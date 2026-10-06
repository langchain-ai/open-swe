"use client";

/*
 * Rules for ChoiceCards.
 *
 * A fork is a set of cards. The chosen card opens one section. Two forms in
 * one view is the failure this pattern exists to prevent.
 */

import type { Glyph } from "../ui/glyphs";
import { Box, Inline, Stack } from "../ui/box";
import { Icon } from "../ui/icon";
import { IconWell } from "../ui/icon-well";
import { HELP_CLASS, LABEL_CLASS } from "../ui/label";
import { RadioGroup, RadioGroupItem } from "../ui/radio-group";

const CHOICE_CARD_RULES: readonly string[] = [
  "A fork is cards, not a radio list and not two forms stacked. Each card names the path and what it opens.",
  "The card is the control. A radio mark never appears. The exclusive group stays for the keyboard and the screen reader.",
  "Selecting a card opens that path alone. Returning to the cards is Back, never a second form appearing underneath.",
  "A card carries an icon, a title, and a description. It never shows keys, schema names, or source types.",
];

interface ChoiceCardOption<Value extends string> {
  description: string;
  icon: Glyph;
  title: string;
  value: Value;
}

interface ChoiceCardsProps<Value extends string> {
  describedBy?: string;
  labelledBy: string;
  onChange: (value: Value) => void;
  options: readonly ChoiceCardOption<Value>[];
  parse: (value: string) => value is Value;
  value: Value | null;
}

function choiceGridClass(count: number): string {
  if (count >= 3) return "grid-cols-1 sm:grid-cols-3";
  return "grid-cols-1 sm:grid-cols-2";
}

function ChoiceCards<Value extends string>({
  describedBy,
  labelledBy,
  onChange,
  options,
  parse,
  value,
}: ChoiceCardsProps<Value>) {
  return (
    <Box data-slot="choice-cards">
      <RadioGroup<Value | "">
        value={value ?? ""}
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        onValueChange={(next) => {
          if (parse(next)) onChange(next);
        }}
        className={choiceGridClass(options.length)}
      >
        {options.map((option) => {
          const id = `choice-${option.value.toLowerCase()}`;
          return (
            <Box
              key={option.value}
              render={<label htmlFor={id} />}
              className="cursor-pointer rounded-compact border border-line-strong bg-panel p-3 transition-[border-color,background-color,transform] duration-fast ease-out-quint hover:bg-hover has-[[data-checked]]:border-primary has-[[data-checked]]:bg-selected has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-primary active:scale-[0.97] motion-reduce:transition-none"
            >
              <Inline gap="sm" align="start">
                <IconWell>
                  <Icon icon={option.icon} size="sm" />
                </IconWell>
                <Stack gap="xs" className="min-w-0 flex-1">
                  <Box
                    render={<span />}
                    className={LABEL_CLASS}
                  >
                    {option.title}
                  </Box>
                  <Box render={<span />} className={HELP_CLASS}>
                    {option.description}
                  </Box>
                </Stack>
                <RadioGroupItem id={id} value={option.value} className="sr-only" />
              </Inline>
            </Box>
          );
        })}
      </RadioGroup>
    </Box>
  );
}

export { ChoiceCards, CHOICE_CARD_RULES };
export type { ChoiceCardOption, ChoiceCardsProps };
