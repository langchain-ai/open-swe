import React from "react";
import type { Key } from "ink";
import {
  BRACKETED_PASTE_END,
  BRACKETED_PASTE_START,
  BRACKETED_PASTE_START_NO_ESC,
  getMacOSClipboardImage,
  imageFilenameFromPath,
  isImageFilePath,
  PASTE_THRESHOLD,
  stripBracketedPasteMarkers,
  TEMP_SCREENSHOT_PATH_RE,
  tryReadImageFromPath,
} from "@lib/text-input/image-paste-utils.js";
import { logError } from "@lib/logger";

const PASTE_COMPLETION_TIMEOUT_MS = 100;
const IS_MACOS = process.platform === "darwin";

type PasteHandlerProps = {
  onPaste?: (text: string) => void;
  onInput: (input: string, key: Key) => void;
  onImagePaste?: (
    base64Image: string,
    mediaType?: string,
    filename?: string,
    sourcePath?: string,
  ) => void;
};

type WrappedOnInput = (input: string, key: Key) => void;

/**
 * Detect bracketed-paste / large-input events and route them away from
 * normal keystroke handling. Splits drag-and-dropped image paths off into
 * `onImagePaste` and forwards everything else to `onPaste` (or `onInput`
 * for short, non-paste input).
 *
 * Standard Ink (6.x) doesn't surface bracketed-paste flags on Key — the
 * `\x1b[200~ … \x1b[201~` markers come through verbatim in `input`. We
 * strip them here, treat anything wrapped in markers as a paste, and on
 * macOS fall back to the system clipboard when the marker pair is empty
 * (⌘V with an image-only clipboard) or when the temporary screenshot file
 * has already been GC'd by `screencaptureui`.
 */
export function usePasteHandler({
  onPaste,
  onInput,
  onImagePaste,
}: PasteHandlerProps): {
  wrappedOnInput: WrappedOnInput;
  isPasting: boolean;
} {
  const [pasteState, setPasteState] = React.useState<{
    chunks: string[];
    timeoutId: ReturnType<typeof setTimeout> | null;
  }>({ chunks: [], timeoutId: null });
  const [isPasting, setIsPasting] = React.useState(false);
  const isMountedRef = React.useRef(true);
  // Mirrors pasteState.timeoutId synchronously. When paste + a keystroke
  // arrive in the same stdin chunk, both wrappedOnInput calls run before
  // React commits — the second call would otherwise read a stale null
  // and route the keystroke (e.g. Enter) through onInput, submitting the
  // old value and losing the paste.
  const pastePendingRef = React.useRef(false);

  React.useEffect(() => {
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const tryClipboardFallback = React.useCallback(async () => {
    if (!onImagePaste || !IS_MACOS) return false;
    const img = await getMacOSClipboardImage();
    if (!img) return false;
    onImagePaste(img.base64, img.mediaType);
    return true;
  }, [onImagePaste]);

  const flushPaste = React.useCallback(
    (chunks: string[]) => {
      pastePendingRef.current = false;
      // Strip bracketed-paste markers before any path-shaped detection: the
      // trailing `\x1b[201~` would otherwise foil `isImageFilePath`'s
      // ".png$" check, which is exactly the failure mode we hit on iTerm2.
      const joined = chunks.join("");
      const stripped = stripBracketedPasteMarkers(joined);
      const pastedText = stripped.text.replace(/\[I$/, "").replace(/\[O$/, "");

      const lines = pastedText
        .split(/ (?=\/|[A-Za-z]:\\)/)
        .flatMap((part) => part.split("\n"))
        .filter((line) => line.trim());
      const imagePaths = lines.filter((line) => isImageFilePath(line));
      const isTempScreenshot = TEMP_SCREENSHOT_PATH_RE.test(pastedText);

      // Empty bracketed paste on macOS = clipboard image (⌘V with an image
      // copied from a screenshot tool, browser, etc.). Read directly from
      // NSPasteboard via osascript.
      if (
        onImagePaste &&
        stripped.hadMarkers &&
        pastedText.length === 0 &&
        IS_MACOS
      ) {
        void tryClipboardFallback().finally(() => {
          if (isMountedRef.current) setIsPasting(false);
        });
        return;
      }

      if (onImagePaste && imagePaths.length > 0) {
        void Promise.all(imagePaths.map((p) => tryReadImageFromPath(p)))
          .then(async (results) => {
            const validImages = results.filter(
              (r): r is NonNullable<typeof r> => r !== null,
            );
            if (validImages.length > 0) {
              for (const img of validImages) {
                onImagePaste(
                  img.base64,
                  img.mediaType,
                  imageFilenameFromPath(img.path),
                  img.path,
                );
              }
              const nonImageLines = lines.filter(
                (line) => !isImageFilePath(line),
              );
              if (nonImageLines.length > 0 && onPaste) {
                onPaste(nonImageLines.join("\n"));
              }
              return;
            }
            // All image paths failed to read. macOS screenshot temp files vanish
            // quickly — try the clipboard before giving up.
            if (isTempScreenshot && (await tryClipboardFallback())) return;
            if (onPaste) onPaste(pastedText);
          })
          .catch((err: unknown) => {
            void logError(
              `paste image read failed: ${err instanceof Error ? err.message : String(err)}`,
            );
            if (onPaste) onPaste(pastedText);
          })
          .finally(() => {
            if (isMountedRef.current) setIsPasting(false);
          });
        return;
      }

      if (onPaste && pastedText.length > 0) onPaste(pastedText);
      setIsPasting(false);
    },
    [onImagePaste, onPaste, tryClipboardFallback],
  );

  const resetPasteTimeout = React.useCallback(
    (currentTimeoutId: ReturnType<typeof setTimeout> | null) => {
      if (currentTimeoutId) clearTimeout(currentTimeoutId);
      return setTimeout(() => {
        setPasteState(({ chunks }) => {
          flushPaste(chunks);
          return { chunks: [], timeoutId: null };
        });
      }, PASTE_COMPLETION_TIMEOUT_MS);
    },
    [flushPaste],
  );

  const wrappedOnInput: WrappedOnInput = (input, key) => {
    // Bracketed paste: the start marker is the most reliable signal. Any
    // subsequent chunks (until we see the end marker) belong to the same
    // paste, so we keep batching for the timeout window.
    //
    // Ink 6 strips a single leading `\u001b` from each input chunk before
    // we see it, so a bracketed-paste that begins this chunk will arrive
    // as `[200~…` (no ESC). Match both forms to avoid routing the paste
    // through normal keystroke handling — which would insert `[200~` as
    // visible text and silently corrupt the user's pasted content.
    const containsPasteStart =
      input.includes(BRACKETED_PASTE_START) ||
      input.startsWith(BRACKETED_PASTE_START_NO_ESC);
    const containsPasteEnd =
      input.includes(BRACKETED_PASTE_END) ||
      // Trailing `[201~` optionally followed by CR/LF when the user pressed
      // Enter inside the same paste burst.
      /\[201~[\r\n]*$/.test(input);

    // Image-path detection on the stripped chunk: terminals that DON'T enable
    // bracketed paste still emit drag-and-drop as one input chunk we can match
    // by extension.
    const stripped = stripBracketedPasteMarkers(input);
    const hasImageFilePath = stripped.text
      .split(/ (?=\/|[A-Za-z]:\\)/)
      .flatMap((part) => part.split("\n"))
      .some((line) => isImageFilePath(line.trim()));

    const shouldHandleAsPaste =
      onPaste &&
      (containsPasteStart ||
        containsPasteEnd ||
        input.length > PASTE_THRESHOLD ||
        pastePendingRef.current ||
        hasImageFilePath);

    if (shouldHandleAsPaste) {
      pastePendingRef.current = true;
      setIsPasting(true);
      setPasteState(({ chunks, timeoutId }) => ({
        chunks: [...chunks, input],
        timeoutId: resetPasteTimeout(timeoutId),
      }));
      return;
    }
    onInput(input, key);
    if (input.length > 10) {
      // Long stdin chunks may straddle a bracketed-paste close sequence —
      // clear isPasting defensively so the UI doesn't get stuck.
      setIsPasting(false);
    }
  };

  void pasteState;
  return { wrappedOnInput, isPasting };
}
