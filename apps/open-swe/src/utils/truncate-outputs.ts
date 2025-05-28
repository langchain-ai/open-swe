export function truncateOutput(
  output: string,
  options?: {
    /**
     * @default 10000
     */
    numStartCharacters?: number;

    /**
     * @default 10000
     */
    numEndCharacters?: number;
  },
) {
  const { numStartCharacters = 10000, numEndCharacters = 10000 } =
    options ?? {};

  if (numStartCharacters < 0 || numEndCharacters < 0) {
    throw new Error("numStartCharacters and numEndCharacters must be >= 0");
  }
  if (!numStartCharacters && !numEndCharacters) {
    throw new Error(
      "At least one of numStartCharacters or numEndCharacters must be > 0",
    );
  }

  if (output.length <= numStartCharacters + numEndCharacters) {
    return output;
  }

  return (
    `The following output was truncated due to its length exceeding the maximum allowed characters. Received ${output.length} characters, but only ${numStartCharacters + numEndCharacters} were allowed.\n\n` +
    output.slice(0, numStartCharacters) +
    `\n\n... [content truncated] ...\n\n` +
    output.slice(-numEndCharacters)
  );
}
