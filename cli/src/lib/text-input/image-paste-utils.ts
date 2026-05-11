import { exec } from "child_process";
import { randomBytes } from "crypto";
import { promises as fs } from "fs";
import { tmpdir } from "os";
import { basename, extname, isAbsolute, join } from "path";
import { promisify } from "util";

const execAsync = promisify(exec);

/** Threshold in characters above which raw input is treated as a "large paste". */
export const PASTE_THRESHOLD = 800;

/** Bracketed-paste start / end sequences emitted by xterm-compatible terminals. */
export const BRACKETED_PASTE_START = "\u001b[200~";
export const BRACKETED_PASTE_END = "\u001b[201~";
/**
 * Ink 6's `use-input.js` strips a single leading `\u001b` from each input
 * chunk before our handler sees it. When a bracketed-paste begins at the
 * start of a chunk, we therefore see the START marker as `[200~` (no ESC).
 * Same applies if the END marker arrives in its own chunk. We strip these
 * "ESC-less" forms in addition to the canonical sequences so users don't
 * end up with a `[200~` prefix glued to the front of their pasted content
 * (most painful when pasting API keys — see GH bug-report).
 */
export const BRACKETED_PASTE_START_NO_ESC = "[200~";
export const BRACKETED_PASTE_END_NO_ESC = "[201~";

/**
 * Image extensions accepted from drag-and-drop or paste. Kept aligned with
 * the MIME map below — adding an entry here without a MIME entry will yield
 * `application/octet-stream` and the LLM will reject it.
 */
export const IMAGE_EXTENSION_REGEX = /\.(png|jpe?g|gif|webp)$/i;

const MIME_BY_EXT: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
};

function removeOuterQuotes(text: string): string {
  if (
    (text.startsWith('"') && text.endsWith('"')) ||
    (text.startsWith("'") && text.endsWith("'"))
  ) {
    return text.slice(1, -1);
  }
  return text;
}

/**
 * Remove shell escape backslashes from a path on macOS/Linux. macOS Terminal
 * drag-and-drop yields paths like `/Users/foo/My\ File.png`; we strip the
 * single-backslash escapes and preserve double-backslashes (literal `\`).
 */
function stripBackslashEscapes(path: string): string {
  if (process.platform === "win32") return path;
  // Random-salted placeholder so paths can't impersonate the sentinel.
  const salt = randomBytes(8).toString("hex");
  const placeholder = `__DOUBLE_BACKSLASH_${salt}__`;
  const withPlaceholder = path.replace(/\\\\/g, placeholder);
  const withoutEscapes = withPlaceholder.replace(/\\(.)/g, "$1");
  return withoutEscapes.replace(new RegExp(placeholder, "g"), "\\");
}

/**
 * Strip bracketed-paste markers from a stdin chunk. Returns the cleaned text
 * and a flag noting whether markers were present.
 *
 * Also handles the "ESC-stripped" forms (`[200~` at start, `[201~` at end)
 * that result from Ink 6 swallowing the leading `\u001b` of the first chunk
 * in a bracketed paste. Without this, every paste begins with a literal
 * `[200~` prefix glued to the user's content.
 */
export function stripBracketedPasteMarkers(text: string): {
  text: string;
  hadMarkers: boolean;
} {
  const hasFullStart = text.includes(BRACKETED_PASTE_START);
  const hasFullEnd = text.includes(BRACKETED_PASTE_END);
  const hasStrippedStart = text.startsWith(BRACKETED_PASTE_START_NO_ESC);
  // Trailing `[201~` may be followed by CR/LF when the user hits Enter
  // inside the same paste burst.
  const hasStrippedEnd = /\[201~[\r\n]*$/.test(text);

  if (!hasFullStart && !hasFullEnd && !hasStrippedStart && !hasStrippedEnd) {
    return { text, hadMarkers: false };
  }

  let cleaned = text;
  // Replace ALL occurrences (a single chunk can contain multiple bracketed
  // pastes when the terminal coalesces fast strokes).
  cleaned = cleaned.split(BRACKETED_PASTE_START).join("");
  cleaned = cleaned.split(BRACKETED_PASTE_END).join("");
  // ESC-stripped markers can only legitimately appear flush against the
  // chunk boundary, modulo a trailing CR/LF when the user hits Enter in the
  // same paste burst. Pulling them from the middle of a string would risk
  // eating real user content that happens to contain `[200~`/`[201~`.
  cleaned = cleaned.replace(
    new RegExp(`^${escapeRegex(BRACKETED_PASTE_START_NO_ESC)}`),
    "",
  );
  cleaned = cleaned.replace(
    new RegExp(`${escapeRegex(BRACKETED_PASTE_END_NO_ESC)}([\\r\\n]*)$`),
    "$1",
  );
  return { text: cleaned, hadMarkers: true };
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Check whether a single drag-and-drop / paste line is an image file path.
 */
export function isImageFilePath(text: string): boolean {
  const cleaned = removeOuterQuotes(text.trim());
  const unescaped = stripBackslashEscapes(cleaned);
  return IMAGE_EXTENSION_REGEX.test(unescaped);
}

/**
 * Strip quotes / escape backslashes and return a clean path if the text looks
 * like an image file path; null otherwise.
 */
export function asImageFilePath(text: string): string | null {
  const cleaned = removeOuterQuotes(text.trim());
  const unescaped = stripBackslashEscapes(cleaned);
  return IMAGE_EXTENSION_REGEX.test(unescaped) ? unescaped : null;
}

/**
 * Match macOS-style temporary screenshot paths (created by the system
 * `screencaptureui` helper). These often disappear before our async
 * `fs.readFile` lands, so callers should fall back to the clipboard when one
 * of these paths fails to read.
 */
export const TEMP_SCREENSHOT_PATH_RE =
  /\/TemporaryItems\/.*screencaptureui.*\/Screenshot/i;

export type ReadImageResult = {
  /** Resolved on-disk absolute path. */
  path: string;
  /** Base64-encoded bytes. */
  base64: string;
  /** MIME type, e.g. "image/png". */
  mediaType: string;
};

/**
 * Try to read an image file from disk by its (drag-and-dropped) path.
 * Returns null if the path is not an image, doesn't exist, or is empty.
 */
export async function tryReadImageFromPath(
  text: string,
): Promise<ReadImageResult | null> {
  const cleanedPath = asImageFilePath(text);
  if (!cleanedPath) return null;
  if (!isAbsolute(cleanedPath)) return null;

  let imageBuffer: Buffer;
  try {
    imageBuffer = await fs.readFile(cleanedPath);
  } catch {
    return null;
  }
  if (imageBuffer.length === 0) return null;

  const ext = extname(cleanedPath).slice(1).toLowerCase();
  const mediaType = MIME_BY_EXT[ext] ?? "image/png";
  return {
    path: cleanedPath,
    base64: imageBuffer.toString("base64"),
    mediaType,
  };
}

/**
 * Get a friendly filename for an image read from a path.
 */
export function imageFilenameFromPath(path: string): string {
  return basename(path);
}

export type ClipboardImage = {
  base64: string;
  mediaType: string;
};

/**
 * Read an image from the macOS clipboard, if one is present. Returns null on
 * non-darwin platforms or when the clipboard has no image.
 *
 * Implementation: shell out to `osascript` to dump the clipboard image as PNG
 * to a temp file, then read it. This is the fallback path for when
 * bracketed-paste arrives empty (⌘V with image-only clipboard) or when the
 * temp screenshot file has already been GC'd by macOS.
 */
export async function getMacOSClipboardImage(): Promise<ClipboardImage | null> {
  if (process.platform !== "darwin") return null;

  // Quick presence check — fast and avoids creating a temp file when there's
  // no image in the clipboard.
  try {
    const check = await execAsync(
      "osascript -e 'the clipboard as «class PNGf»'",
      { timeout: 2000 },
    );
    if (typeof check.stdout !== "string" || check.stdout.length === 0)
      return null;
  } catch {
    return null;
  }

  const screenshotPath = join(
    tmpdir(),
    `coda_clipboard_${randomBytes(4).toString("hex")}.png`,
  );

  try {
    await execAsync(
      `osascript -e 'set png_data to (the clipboard as «class PNGf»)' \
        -e 'set fp to open for access POSIX file "${screenshotPath}" with write permission' \
        -e 'write png_data to fp' \
        -e 'close access fp'`,
      { timeout: 5000 },
    );
    const buffer = await fs.readFile(screenshotPath);
    if (buffer.length === 0) return null;
    return {
      base64: buffer.toString("base64"),
      mediaType: "image/png",
    };
  } catch {
    return null;
  } finally {
    void fs.unlink(screenshotPath).catch(() => {});
  }
}
