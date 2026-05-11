import chalk from "chalk";

type PlaceholderRendererProps = {
  placeholder?: string;
  value: string;
  showCursor?: boolean;
  focus?: boolean;
  invert?: (text: string) => string;
};

/**
 * Render placeholder text. When the input is focused with cursor visible we
 * invert the first character to emulate a caret on the placeholder.
 */
export function renderPlaceholder({
  placeholder,
  value,
  showCursor,
  focus,
  invert = chalk.inverse,
}: PlaceholderRendererProps): {
  renderedPlaceholder: string | undefined;
  showPlaceholder: boolean;
} {
  let renderedPlaceholder: string | undefined;

  if (placeholder) {
    renderedPlaceholder = chalk.dim(placeholder);
    if (showCursor && focus) {
      renderedPlaceholder =
        placeholder.length > 0
          ? invert(placeholder[0]!) + chalk.dim(placeholder.slice(1))
          : invert(" ");
    }
  }

  const showPlaceholder = value.length === 0 && Boolean(placeholder);
  return { renderedPlaceholder, showPlaceholder };
}
