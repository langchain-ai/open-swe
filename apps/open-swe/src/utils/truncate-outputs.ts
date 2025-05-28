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
    output.slice(0, numStartCharacters) +
    `\n... Output too long. Truncated the middle ${output.length - numStartCharacters - numEndCharacters} characters of the output ...\n` +
    output.slice(-numEndCharacters)
  );
}
