import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { promises as fs } from "fs";
import path from "path";
import os from "os";
import {
  pruneImages,
  buildHumanMessageWithImages,
  readImageFromPath,
  type ImageRef,
} from "../image-paste.js";
import {
  asImageFilePath,
  BRACKETED_PASTE_END,
  BRACKETED_PASTE_START,
  isImageFilePath,
  stripBracketedPasteMarkers,
  TEMP_SCREENSHOT_PATH_RE,
  tryReadImageFromPath,
} from "../text-input/image-paste-utils.js";

let tmpDir: string;
let pngPath: string;
let pngWithSpacePath: string;
let jpgPath: string;

const PNG_BYTES = Buffer.from(
  "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da6300010000000500010d0a2db40000000049454e44ae426082",
  "hex",
);

beforeAll(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "coda-img-test-"));
  pngPath = path.join(tmpDir, "a.png");
  pngWithSpacePath = path.join(tmpDir, "spaced name.png");
  jpgPath = path.join(tmpDir, "b.jpg");
  await fs.writeFile(pngPath, PNG_BYTES);
  await fs.writeFile(pngWithSpacePath, PNG_BYTES);
  await fs.writeFile(jpgPath, PNG_BYTES);
});

afterAll(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

describe("stripBracketedPasteMarkers", () => {
  it("removes both markers around a path and reports them as present", () => {
    const wrapped = `${BRACKETED_PASTE_START}/tmp/foo.png${BRACKETED_PASTE_END}`;
    const out = stripBracketedPasteMarkers(wrapped);
    expect(out.text).toBe("/tmp/foo.png");
    expect(out.hadMarkers).toBe(true);
  });

  it("handles a stray end marker", () => {
    const out = stripBracketedPasteMarkers(`hi${BRACKETED_PASTE_END}`);
    expect(out.text).toBe("hi");
    expect(out.hadMarkers).toBe(true);
  });

  it("returns text untouched when no markers are present", () => {
    const out = stripBracketedPasteMarkers("plain text");
    expect(out.text).toBe("plain text");
    expect(out.hadMarkers).toBe(false);
  });

  it("strips multiple bracketed pastes coalesced into one chunk", () => {
    const wrapped = `${BRACKETED_PASTE_START}a${BRACKETED_PASTE_END}${BRACKETED_PASTE_START}b${BRACKETED_PASTE_END}`;
    const out = stripBracketedPasteMarkers(wrapped);
    expect(out.text).toBe("ab");
    expect(out.hadMarkers).toBe(true);
  });

  it("strips an ESC-less START marker at the beginning (Ink 6 swallows the leading \\x1b)", () => {
    // Reproduces the original API-key corruption bug: bracketed paste arrives
    // with the leading ESC stripped by Ink, so the start marker shows up as
    // `[200~` and the canonical strip leaves it glued to the user's content.
    const ink = `[200~sk-proj-IBqjslABCDEF${BRACKETED_PASTE_END}`;
    const out = stripBracketedPasteMarkers(ink);
    expect(out.text).toBe("sk-proj-IBqjslABCDEF");
    expect(out.hadMarkers).toBe(true);
  });

  it("strips an ESC-less END marker arriving in its own chunk", () => {
    const ink = "[201~";
    const out = stripBracketedPasteMarkers(ink);
    expect(out.text).toBe("");
    expect(out.hadMarkers).toBe(true);
  });

  it("strips an ESC-less END marker even when followed by a trailing CR/LF", () => {
    // Multi-chunk paste ending with `\x1b[201~\r` — Ink swallows the leading
    // ESC of the END marker too, leaving `[201~\r` glued to the user's text.
    const ink = "[200~sk-proj-IBqjslABCDEF[201~\r";
    const out = stripBracketedPasteMarkers(ink);
    expect(out.text).toBe("sk-proj-IBqjslABCDEF\r");
    expect(out.hadMarkers).toBe(true);
  });

  it("only strips ESC-less markers at chunk boundaries (not mid-string)", () => {
    // `[200~` mid-string is real user content (e.g., a regression test name)
    // and must be preserved.
    const out = stripBracketedPasteMarkers("hello [200~ world");
    expect(out.text).toBe("hello [200~ world");
    expect(out.hadMarkers).toBe(false);
  });
});

describe("isImageFilePath after marker stripping", () => {
  it("image path wrapped in bracketed-paste markers is only matched after stripping", () => {
    const wrapped = `${BRACKETED_PASTE_START}/Users/me/img.png${BRACKETED_PASTE_END}`;
    // Regression we're guarding: pre-strip matching used to fail because the
    // \x1b[201~ trailer would foil the `.png$` regex, leaving the user with
    // their image path inserted as plain text.
    expect(isImageFilePath(wrapped)).toBe(false);
    const { text } = stripBracketedPasteMarkers(wrapped);
    expect(isImageFilePath(text)).toBe(true);
  });
});

describe("TEMP_SCREENSHOT_PATH_RE", () => {
  it("matches macOS screencaptureui temporary screenshot paths", () => {
    const path =
      "/var/folders/85/x_t5/T/TemporaryItems/IRD_screencaptureui_KCNHo8/Screenshot 2026-05-09 at 10.42.10 AM.png";
    expect(TEMP_SCREENSHOT_PATH_RE.test(path)).toBe(true);
  });

  it("does not match arbitrary paths", () => {
    expect(TEMP_SCREENSHOT_PATH_RE.test("/Users/me/Pictures/regular.png")).toBe(
      false,
    );
  });
});

describe("isImageFilePath", () => {
  it("detects plain absolute paths", () => {
    expect(isImageFilePath(pngPath)).toBe(true);
    expect(isImageFilePath(jpgPath)).toBe(true);
  });

  it("detects single-quoted paths with spaces", () => {
    expect(isImageFilePath(`'${pngWithSpacePath}'`)).toBe(true);
  });

  it("detects double-quoted paths with spaces", () => {
    expect(isImageFilePath(`"${pngWithSpacePath}"`)).toBe(true);
  });

  it("detects backslash-escaped spaces", () => {
    const escaped = pngWithSpacePath.replace(/ /g, "\\ ");
    expect(isImageFilePath(escaped)).toBe(true);
  });

  it("rejects non-image paths", () => {
    expect(isImageFilePath("/tmp/foo.txt")).toBe(false);
    expect(isImageFilePath("plain text without extension")).toBe(false);
  });
});

describe("asImageFilePath", () => {
  it("returns clean path for image-like text", () => {
    expect(asImageFilePath(`'${pngWithSpacePath}'`)).toBe(pngWithSpacePath);
    expect(asImageFilePath(pngPath)).toBe(pngPath);
  });

  it("returns null for non-image text", () => {
    expect(asImageFilePath("hello world")).toBe(null);
  });
});

describe("tryReadImageFromPath", () => {
  it("reads existing PNG and returns base64 + media type", async () => {
    const result = await tryReadImageFromPath(pngPath);
    expect(result).not.toBe(null);
    expect(result?.path).toBe(pngPath);
    expect(result?.mediaType).toBe("image/png");
    expect(result?.base64.length).toBeGreaterThan(0);
  });

  it("reads existing JPG with correct media type", async () => {
    const result = await tryReadImageFromPath(jpgPath);
    expect(result?.mediaType).toBe("image/jpeg");
  });

  it("returns null for non-existent path", async () => {
    const missing = path.join(tmpDir, "does-not-exist.png");
    const result = await tryReadImageFromPath(missing);
    expect(result).toBe(null);
  });

  it("returns null for non-image text", async () => {
    const result = await tryReadImageFromPath("hello world");
    expect(result).toBe(null);
  });
});

describe("readImageFromPath", () => {
  it("reads image bytes and infers media type", async () => {
    const result = await readImageFromPath(pngPath);
    expect(result).not.toBe(null);
    expect(result?.mediaType).toBe("image/png");
    expect(result?.filename).toBe("a.png");
    expect(result?.sourcePath).toBe(pngPath);
  });

  it("returns null when the file is missing", async () => {
    const result = await readImageFromPath(path.join(tmpDir, "nope.png"));
    expect(result).toBe(null);
  });
});

describe("pruneImages", () => {
  it("drops entries whose placeholder is no longer present", () => {
    const images = new Map<number, ImageRef>([
      [1, { index: 1, base64: "abc", mediaType: "image/png" }],
      [2, { index: 2, base64: "def", mediaType: "image/png" }],
    ]);
    pruneImages("only [Image #2] left", images);
    expect(images.has(1)).toBe(false);
    expect(images.has(2)).toBe(true);
  });
});

describe("buildHumanMessageWithImages", () => {
  it("returns plain text message when no images attached", async () => {
    const msg = await buildHumanMessageWithImages("hello", new Map());
    expect(msg.content).toBe("hello");
  });

  it("builds multipart content with image_url parts", async () => {
    const images = new Map<number, ImageRef>([
      [1, { index: 1, base64: "AAAA", mediaType: "image/png" }],
    ]);
    const msg = await buildHumanMessageWithImages(
      "describe [Image #1]",
      images,
    );
    expect(Array.isArray(msg.content)).toBe(true);
    const parts = msg.content as {
      type: string;
      image_url?: { url: string };
      text?: string;
    }[];
    expect(parts[0]).toEqual({ type: "text", text: "describe [Image #1]" });
    expect(parts[1].type).toBe("image_url");
    expect(parts[1].image_url?.url).toMatch(/^data:image\/png;base64,/);
  });

  it("orders image parts by index", async () => {
    const images = new Map<number, ImageRef>([
      [2, { index: 2, base64: "BBBB", mediaType: "image/jpeg" }],
      [1, { index: 1, base64: "AAAA", mediaType: "image/png" }],
    ]);
    const msg = await buildHumanMessageWithImages(
      "a [Image #1] [Image #2]",
      images,
    );
    const parts = msg.content as {
      type: string;
      image_url?: { url: string };
    }[];
    expect(parts[1].image_url?.url).toMatch(/^data:image\/png/);
    expect(parts[2].image_url?.url).toMatch(/^data:image\/jpeg/);
  });
});
