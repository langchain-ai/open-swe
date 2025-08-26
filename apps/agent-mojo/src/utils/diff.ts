import { createLogger, LogLevel } from "./logger.js";

const logger = createLogger(LogLevel.INFO, "DiffUtil");

interface Hunk {
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  context: string;
  lines: string[];
}

interface PatchFile {
  oldFile: string;
  newFile: string | null;
  hunks: Hunk[];
}

interface ParsedPatch {
  files: PatchFile[];
}

interface FileContents {
  [filename: string]: string;
}

export function fixGitPatch(
  patchString: string,
  fileContents: FileContents,
): string {
  // First, normalize the patch string - convert literal \n to actual newlines if needed
  const normalizedPatch: string = patchString.includes("\\n")
    ? patchString.replace(/\\n/g, "\n")
    : patchString;

  // Parse patch into structured format
  function parsePatch(patch: string): ParsedPatch {
    const lines: string[] = patch
      .split("\n")
      .filter((line): line is string => line !== undefined);
    const result: ParsedPatch = {
      files: [],
    };

    let currentFile: PatchFile | null = null;
    let currentHunk: Hunk | null = null;
    let i: number = 0;

    while (i < lines.length) {
      const line: string = lines[i];

      // Skip empty lines between files
      if (!line && !currentHunk) {
        i++;
        continue;
      }

      // File header
      if (line.startsWith("--- ")) {
        if (currentFile && currentFile.hunks.length > 0) {
          result.files.push(currentFile);
        }
        // Handle both --- a/file and --- file formats
        const filename: string = line.startsWith("--- a/")
          ? line.substring(6)
          : line.substring(4);
        currentFile = {
          oldFile: filename,
          newFile: null,
          hunks: [],
        };
        currentHunk = null;
        i++;
        continue;
      }

      if (line.startsWith("+++ ") && currentFile) {
        // Handle both +++ b/file and +++ file formats
        currentFile.newFile = line.startsWith("+++ b/")
          ? line.substring(6)
          : line.substring(4);
        i++;
        continue;
      }

      // Hunk header
      if (line.startsWith("@@")) {
        const match: RegExpMatchArray | null = line.match(
          /@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)/,
        );
        if (match) {
          currentHunk = {
            oldStart: parseInt(match[1]),
            oldLines: parseInt(match[2] || "1"),
            newStart: parseInt(match[3]),
            newLines: parseInt(match[4] || "1"),
            context: match[5] || "",
            lines: [],
          };
          if (currentFile) {
            currentFile.hunks.push(currentHunk);
          }
        }
        i++;
        continue;
      }

      // Hunk content
      if (currentHunk) {
        // For diff content, include all lines that are part of the diff
        if (
          line.startsWith(" ") ||
          line.startsWith("+") ||
          line.startsWith("-")
        ) {
          currentHunk.lines.push(line);
        }
      }

      i++;
    }

    if (currentFile && currentFile.hunks.length > 0) {
      result.files.push(currentFile);
    }

    return result;
  }

  // Get file content as array of lines
  function getFileLines(filename: string, contents: FileContents): string[] {
    // Handle /dev/null for new files
    if (filename === "/dev/null") {
      return [];
    }

    // Try multiple variations of the filename
    const variations: string[] = [
      filename,
      filename.replace(/^\.\//, ""),
      "./" + filename,
      filename.replace(/^\//, ""),
      filename.replace(/^a\//, ""),
      filename.replace(/^b\//, ""),
    ];

    for (const variant of variations) {
      if (variant in contents) {
        return contents[variant].split("\n");
      }
    }

    return [];
  }

  // Check if this is a new file creation
  function isNewFile(hunk: Hunk): boolean {
    return hunk.oldStart === 0 && hunk.oldLines === 0;
  }

  // Check if this is a file deletion
  function isFileDeleted(hunk: Hunk): boolean {
    return hunk.newStart === 0 && hunk.newLines === 0;
  }

  // Fix a single hunk
  function fixHunk(hunk: Hunk, fileLines: string[]): Hunk {
    // For new files, just validate line counts
    if (isNewFile(hunk)) {
      let newCount: number = 0;
      for (const line of hunk.lines) {
        if (line.startsWith("+")) {
          newCount++;
        }
      }

      return {
        oldStart: 0,
        oldLines: 0,
        newStart: 1,
        newLines: newCount,
        context: hunk.context,
        lines: [...hunk.lines],
      };
    }

    // For file deletions
    if (isFileDeleted(hunk)) {
      let oldCount: number = 0;
      for (const line of hunk.lines) {
        if (line.startsWith("-")) {
          oldCount++;
        }
      }

      return {
        oldStart: hunk.oldStart,
        oldLines: oldCount,
        newStart: 0,
        newLines: 0,
        context: hunk.context,
        lines: [...hunk.lines],
      };
    }

    // For regular modifications
    // Extract context and removed lines for matching
    const matchLines: string[] = [];
    for (const line of hunk.lines) {
      if (line.startsWith(" ") || line.startsWith("-")) {
        matchLines.push(line.substring(1));
      }
    }

    // Find where this hunk actually belongs
    let actualStart: number = -1;
    if (matchLines.length > 0 && fileLines.length > 0) {
      actualStart = findBestMatch(fileLines, matchLines, hunk.oldStart);
    }

    // Count actual old and new lines
    let oldCount: number = 0;
    let newCount: number = 0;

    for (const line of hunk.lines) {
      if (line.startsWith(" ")) {
        oldCount++;
        newCount++;
      } else if (line.startsWith("-")) {
        oldCount++;
      } else if (line.startsWith("+")) {
        newCount++;
      }
    }

    // Build fixed hunk
    return {
      oldStart: actualStart >= 0 ? actualStart + 1 : hunk.oldStart,
      oldLines: oldCount,
      newStart: actualStart >= 0 ? actualStart + 1 : hunk.newStart,
      newLines: newCount,
      context: hunk.context,
      lines: [...hunk.lines],
    };
  }

  // Find best match for lines in file
  function findBestMatch(
    fileLines: string[],
    searchLines: string[],
    startHint: number,
  ): number {
    if (searchLines.length === 0) {
      return startHint - 1;
    }

    // First try exact position
    if (matchesAt(fileLines, searchLines, startHint - 1)) {
      return startHint - 1;
    }

    // Search nearby lines
    const searchRadius: number = Math.min(100, fileLines.length);
    for (let offset: number = 1; offset <= searchRadius; offset++) {
      // Try before
      if (
        startHint - 1 - offset >= 0 &&
        matchesAt(fileLines, searchLines, startHint - 1 - offset)
      ) {
        return startHint - 1 - offset;
      }
      // Try after
      if (
        startHint - 1 + offset < fileLines.length &&
        matchesAt(fileLines, searchLines, startHint - 1 + offset)
      ) {
        return startHint - 1 + offset;
      }
    }

    // Search entire file
    for (let i: number = 0; i <= fileLines.length - searchLines.length; i++) {
      if (matchesAt(fileLines, searchLines, i)) {
        return i;
      }
    }

    return -1;
  }

  // Check if lines match at position
  function matchesAt(
    fileLines: string[],
    searchLines: string[],
    position: number,
  ): boolean {
    if (position < 0 || position + searchLines.length > fileLines.length) {
      return false;
    }

    for (let i: number = 0; i < searchLines.length; i++) {
      if (fileLines[position + i].trim() !== searchLines[i].trim()) {
        return false;
      }
    }
    return true;
  }

  // Rebuild patch string
  function buildPatch(patchData: ParsedPatch): string {
    const result: string[] = [];

    for (const file of patchData.files) {
      // Use the exact format from the original patch
      if (file.oldFile.startsWith("./") || file.oldFile.includes("/")) {
        result.push(`--- a/${file.oldFile}`);
        result.push(`+++ b/${file.newFile}`);
      } else {
        result.push(`--- ${file.oldFile}`);
        result.push(`+++ ${file.newFile}`);
      }

      let cumulativeOffset: number = 0;

      for (const hunk of file.hunks) {
        // For new files, keep newStart at 1
        let adjustedNewStart: number = hunk.newStart;
        if (!isNewFile(hunk) && !isFileDeleted(hunk)) {
          adjustedNewStart = hunk.newStart + cumulativeOffset;
        }

        // Build hunk header
        let header: string = `@@ -${hunk.oldStart}`;
        if (hunk.oldLines !== 1 || hunk.oldStart === 0) {
          header += `,${hunk.oldLines}`;
        }
        header += ` +${adjustedNewStart}`;
        if (hunk.newLines !== 1 || adjustedNewStart === 0) {
          header += `,${hunk.newLines}`;
        }
        header += ` @@`;
        if (hunk.context) {
          header += hunk.context;
        }
        result.push(header);

        // Add hunk lines
        for (const line of hunk.lines) {
          result.push(line);
        }

        // Update cumulative offset
        if (!isNewFile(hunk) && !isFileDeleted(hunk)) {
          cumulativeOffset += hunk.newLines - hunk.oldLines;
        }
      }
    }

    return result.join("\n");
  }

  // Main logic
  try {
    const parsed: ParsedPatch = parsePatch(normalizedPatch);

    if (parsed.files.length === 0) {
      return patchString;
    }

    for (const file of parsed.files) {
      const fileLines: string[] = getFileLines(file.oldFile, fileContents);
      const fixedHunks: Hunk[] = [];

      for (const hunk of file.hunks) {
        const fixedHunk: Hunk = fixHunk(hunk, fileLines);
        if (fixedHunk) {
          fixedHunks.push(fixedHunk);
        }
      }

      file.hunks = fixedHunks;
    }

    const result: string = buildPatch(parsed);

    // More robust check for patches that use literal \n as line separators
    const usesLiteralNewlines =
      /^[^\\]*\\n/.test(patchString) && patchString.split("\n").length === 1;

    if (usesLiteralNewlines && !result.includes("\\n")) {
      return result.replace(/\n/g, "\\n");
    }

    return result;
  } catch (e) {
    logger.error(`Error fixing patch:`, {
      ...(e instanceof Error
        ? { name: e.name, message: e.message, stack: e.stack }
        : { error: e }),
    });
    return patchString;
  }
}
