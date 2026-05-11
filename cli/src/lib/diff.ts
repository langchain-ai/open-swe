import type { DiffLine } from '@types';

const findLCS = (original: string[], updated: string[]): [number, number][] => {
  const table = Array(original.length + 1)
    .fill(null)
    .map(() => Array(updated.length + 1).fill(0));

  for (let i = 1; i <= original.length; i++) {
    for (let j = 1; j <= updated.length; j++) {
      if (original[i - 1] === updated[j - 1]) {
        table[i][j] = table[i - 1][j - 1] + 1;
      } else {
        table[i][j] = Math.max(table[i - 1][j], table[i][j - 1]);
      }
    }
  }

  const indices: [number, number][] = [];
  let i = original.length;
  let j = updated.length;

  while (i > 0 && j > 0) {
    if (original[i - 1] === updated[j - 1]) {
      indices.unshift([i - 1, j - 1]);
      i--;
      j--;
    } else if (table[i - 1][j] > table[i][j - 1]) {
      i--;
    } else {
      j--;
    }
  }

  return indices;
};

export const buildDiffLines = (original: string[], updated: string[]): DiffLine[] => {
  const lcs = findLCS(original, updated);
  const diffLines: DiffLine[] = [];
  let originalIndex = 0;
  let updatedIndex = 0;
  let lcsIndex = 0;

  while (originalIndex < original.length || updatedIndex < updated.length) {
    const match = lcsIndex < lcs.length ? lcs[lcsIndex] : null;

    if (match && match[0] === originalIndex && match[1] === updatedIndex) {
      diffLines.push({
        type: 'context',
        oldLine: originalIndex + 1,
        newLine: updatedIndex + 1,
        text: original[originalIndex],
      });
      originalIndex++;
      updatedIndex++;
      lcsIndex++;
    } else if (match && match[0] === originalIndex) {
      diffLines.push({ type: 'add', newLine: updatedIndex + 1, text: updated[updatedIndex] });
      updatedIndex++;
    } else if (match && match[1] === updatedIndex) {
      diffLines.push({ type: 'remove', oldLine: originalIndex + 1, text: original[originalIndex] });
      originalIndex++;
    } else if (originalIndex < original.length) {
      diffLines.push({ type: 'remove', oldLine: originalIndex + 1, text: original[originalIndex] });
      originalIndex++;
    } else if (updatedIndex < updated.length) {
      diffLines.push({ type: 'add', newLine: updatedIndex + 1, text: updated[updatedIndex] });
      updatedIndex++;
    }
  }

  return diffLines;
};

export function applyPatch(originalContent: string, patch: string): { newContent: string } {
    let lines = originalContent.split('\n');
    const patchLines = patch.split('\n');

    // Extract hunks from patch
    const hunks: string[][] = [];
    let currentHunk: string[] = [];
    let inHunk = false;

    for (const line of patchLines) {
        if (line.startsWith('@@')) {
            if (currentHunk.length > 0) {
                hunks.push(currentHunk);
            }
            currentHunk = [line];
            inHunk = true;
        } else if (inHunk && (line.startsWith(' ') || line.startsWith('+') || line.startsWith('-'))) {
            currentHunk.push(line);
        } else if (line.startsWith('---') || line.startsWith('+++') || line.startsWith('Index:')) {
            // Skip header lines
            continue;
        }
    }
    if (currentHunk.length > 0) {
        hunks.push(currentHunk);
    }

    // Process hunks in reverse order to maintain line indices
    for (let i = hunks.length - 1; i >= 0; i--) {
        const hunk = hunks[i];
        const header = hunk[0];
        const match = /@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(header);

        if (!match) {
            throw new Error(`Invalid hunk header: ${header}`);
        }

        const oldStart = parseInt(match[1], 10) - 1; // Convert to 0-based
        const hunkLines = hunk.slice(1);

        // Build the new section from the hunk
        const newSection: string[] = [];
        let oldLineIndex = oldStart;

        for (const line of hunkLines) {
            if (line.startsWith(' ')) {
                // Context line - include as-is
                newSection.push(line.substring(1));
                oldLineIndex++;
            } else if (line.startsWith('-')) {
                // Remove line - skip it, advance old index
                oldLineIndex++;
            } else if (line.startsWith('+')) {
                // Add line - include in new section
                newSection.push(line.substring(1));
            }
        }

        // Count how many old lines this hunk replaces
        let oldLineCount = 0;
        for (const line of hunkLines) {
            if (line.startsWith(' ') || line.startsWith('-')) {
                oldLineCount++;
            }
        }

        // Replace the old lines with the new section
        lines.splice(oldStart, oldLineCount, ...newSection);
    }

    let result = lines.join('\n');

    // Handle special case: if original was empty and we're creating a file
    if (originalContent === '' && result.endsWith('\n')) {
        result = result.slice(0, -1);
    }

    return { newContent: result };
}

