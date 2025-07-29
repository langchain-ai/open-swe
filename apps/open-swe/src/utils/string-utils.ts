/**
 * Escapes regex metacharacters in a string to treat them as literal characters.
 * Useful for safely converting user input into regex patterns.
 *
 * @param string - The string to escape
 * @returns The escaped string safe for use in RegExp constructor
 *
 * @example
 * escapeRegExp("hello.world") // "hello\\.world"
 * escapeRegExp("test*file") // "test\\*file"
 * escapeRegExp("path[0]") // "path\\[0\\]"
 */
export function escapeRegExp(string: string): string {
  return string.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
