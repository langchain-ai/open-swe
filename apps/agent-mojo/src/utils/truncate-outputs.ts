import { createLogger, LogLevel } from "./logger.js";

const logger = createLogger(LogLevel.INFO, "TruncateOutputs");

export function truncateOutput(
  output: string,
  options?: {
    /**
     * @default 2500
     */
    numStartCharacters?: number;

    /**
     * @default 2500
     */
    numEndCharacters?: number;
  },
) {
  const { numStartCharacters = 2500, numEndCharacters = 2500 } = options ?? {};

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

  logger.warn(
    `Truncating output due to its length exceeding the maximum allowed characters. Received ${output.length} characters, but only ${numStartCharacters + numEndCharacters} were allowed.`,
    {
      numAllowedStartCharacters: numStartCharacters,
      numAllowedEndCharacters: numEndCharacters,
      outputLength: output.length,
    },
  );

  return (
    `The following output was truncated due to its length exceeding the maximum allowed characters. Received ${output.length} characters, but only ${numStartCharacters + numEndCharacters} were allowed.\n\n` +
    output.slice(0, numStartCharacters) +
    `\n\n... [content truncated] ...\n\n` +
    output.slice(-numEndCharacters)
  );
}
