import { promises as fs } from "fs";
import path from "path";
import { HumanMessage } from "@langchain/core/messages";

/**
 * An image attached to the prompt. Created by the paste handler on
 * drag-and-drop / clipboard image paste, referenced in the input as
 * `[Image #N]` and converted to a multipart message part on submit.
 */
export type ImageRef = {
  /** 1-based index shown to the user (e.g. [Image #1]) */
  index: number;
  /** Base64-encoded image data. */
  base64: string;
  /** MIME type, e.g. "image/png". */
  mediaType: string;
  /** Optional human-readable name (used for the OpenAI/Anthropic SDK only). */
  filename?: string;
  /** Optional original on-disk path, useful for debugging only. */
  sourcePath?: string;
};

/**
 * Drops entries from `images` whose `[Image #N]` placeholder no longer appears
 * in the current input value (e.g. user backspaced the placeholder out).
 */
export function pruneImages(
  value: string,
  images: Map<number, ImageRef>,
): void {
  for (const idx of [...images.keys()]) {
    if (!value.includes(`[Image #${idx}]`)) images.delete(idx);
  }
}

/**
 * Build a HumanMessage suitable for sending to the LLM. If `images` is empty,
 * returns a plain-text HumanMessage. Otherwise returns a multipart message
 * with text + image_url parts (data: URLs).
 */
export async function buildHumanMessageWithImages(
  text: string,
  images: Map<number, ImageRef>,
): Promise<HumanMessage> {
  if (images.size === 0) return new HumanMessage(text);

  const imageParts: Array<{
    type: "image_url";
    image_url: { url: string };
  }> = [];

  const ordered = [...images.values()].sort((a, b) => a.index - b.index);
  for (const ref of ordered) {
    const dataUrl = `data:${ref.mediaType};base64,${ref.base64}`;
    imageParts.push({ type: "image_url", image_url: { url: dataUrl } });
  }

  return new HumanMessage({
    content: [{ type: "text", text }, ...imageParts],
  });
}

/**
 * Read an image from disk and return an ImageRef-shaped object (without `index`,
 * which is assigned by the caller). Used as a fallback when callers need to
 * resolve a path-style image reference (kept for tests / scripts).
 */
export async function readImageFromPath(
  absPath: string,
): Promise<Omit<ImageRef, "index"> | null> {
  try {
    const bytes = await fs.readFile(absPath);
    const ext = path.extname(absPath).slice(1).toLowerCase();
    const mediaType = MIME_BY_EXT[ext] ?? "application/octet-stream";
    return {
      base64: bytes.toString("base64"),
      mediaType,
      filename: path.basename(absPath),
      sourcePath: absPath,
    };
  } catch {
    return null;
  }
}

const MIME_BY_EXT: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  bmp: "image/bmp",
};
