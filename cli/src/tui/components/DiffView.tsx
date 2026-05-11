import { Box, Text } from "ink";
import type { ReactNode } from "react";
import { useMemo } from "react";
import { stringWidth } from "@lib/text-input/string-width.js";
import { wrapAnsi } from "@lib/text-input/wrap-ansi.js";
import { diffLinesToStructuredHunk } from "@lib/structured-diff.js";
import { themeColor, type Theme } from "@tui/theme.js";
import type { DiffViewProps, StructuredPatchHunk } from "@types";

type LineType = "add" | "remove" | "nochange";

type LineObject = {
  code: string;
  i: number;
  type: LineType;
  originalCode: string;
  wordDiff?: boolean;
  matchedLine?: LineObject;
};

type NumberedDiffLine = LineObject & {
  matchedLine?: NumberedDiffLine | LineObject;
};

type DiffPart = {
  added?: boolean;
  removed?: boolean;
  value: string;
};

const CHANGE_THRESHOLD = 0.4;
const KEYWORDS = new Set([
  "as",
  "async",
  "await",
  "break",
  "case",
  "catch",
  "class",
  "const",
  "continue",
  "default",
  "do",
  "else",
  "export",
  "extends",
  "false",
  "finally",
  "for",
  "from",
  "function",
  "if",
  "import",
  "in",
  "instanceof",
  "interface",
  "let",
  "new",
  "null",
  "of",
  "return",
  "switch",
  "throw",
  "true",
  "try",
  "type",
  "undefined",
  "while",
]);

function countChangedLines(hunks: StructuredPatchHunk[]) {
  return hunks.reduce(
    (acc, hunk) => {
      for (const line of hunk.lines) {
        if (line.startsWith("+")) acc.additions++;
        if (line.startsWith("-")) acc.removals++;
      }
      return acc;
    },
    { additions: 0, removals: 0 },
  );
}

function transformLinesToObjects(lines: string[]): LineObject[] {
  return lines.map((code) => {
    if (code.startsWith("+")) {
      return {
        code: code.slice(1),
        i: 0,
        type: "add",
        originalCode: code.slice(1),
      };
    }
    if (code.startsWith("-")) {
      return {
        code: code.slice(1),
        i: 0,
        type: "remove",
        originalCode: code.slice(1),
      };
    }
    return {
      code: code.slice(1),
      i: 0,
      type: "nochange",
      originalCode: code.slice(1),
    };
  });
}

function processAdjacentLines(lineObjects: LineObject[]): LineObject[] {
  const processedLines: LineObject[] = [];
  let i = 0;
  while (i < lineObjects.length) {
    const current = lineObjects[i];
    if (!current) {
      i++;
      continue;
    }

    if (current.type === "remove") {
      const removeLines: LineObject[] = [current];
      let j = i + 1;

      while (j < lineObjects.length && lineObjects[j]?.type === "remove") {
        const line = lineObjects[j];
        if (line) removeLines.push(line);
        j++;
      }

      const addLines: LineObject[] = [];
      while (j < lineObjects.length && lineObjects[j]?.type === "add") {
        const line = lineObjects[j];
        if (line) addLines.push(line);
        j++;
      }

      if (removeLines.length > 0 && addLines.length > 0) {
        const pairCount = Math.min(removeLines.length, addLines.length);
        for (let k = 0; k < pairCount; k++) {
          const removeLine = removeLines[k];
          const addLine = addLines[k];
          if (removeLine && addLine) {
            removeLine.wordDiff = true;
            addLine.wordDiff = true;
            removeLine.matchedLine = addLine;
            addLine.matchedLine = removeLine;
          }
        }
        processedLines.push(...removeLines, ...addLines);
        i = j;
      } else {
        processedLines.push(current);
        i++;
      }
    } else {
      processedLines.push(current);
      i++;
    }
  }
  return processedLines;
}

function tokenizeWords(text: string): string[] {
  return text.match(/(\s+|[A-Za-z0-9_$]+|[^\sA-Za-z0-9_$]+)/g) ?? [];
}

function calculateWordDiffs(oldText: string, newText: string): DiffPart[] {
  const oldTokens = tokenizeWords(oldText);
  const newTokens = tokenizeWords(newText);
  const table = Array(oldTokens.length + 1)
    .fill(null)
    .map(() => Array(newTokens.length + 1).fill(0));

  for (let i = 1; i <= oldTokens.length; i++) {
    for (let j = 1; j <= newTokens.length; j++) {
      table[i][j] =
        oldTokens[i - 1] === newTokens[j - 1]
          ? table[i - 1][j - 1] + 1
          : Math.max(table[i - 1][j], table[i][j - 1]);
    }
  }

  const parts: DiffPart[] = [];
  let i = oldTokens.length;
  let j = newTokens.length;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldTokens[i - 1] === newTokens[j - 1]) {
      parts.unshift({ value: oldTokens[i - 1] });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || table[i][j - 1] >= table[i - 1][j])) {
      parts.unshift({ value: newTokens[j - 1], added: true });
      j--;
    } else if (i > 0) {
      parts.unshift({ value: oldTokens[i - 1], removed: true });
      i--;
    }
  }

  return mergeAdjacentParts(parts);
}

function mergeAdjacentParts(parts: DiffPart[]): DiffPart[] {
  const merged: DiffPart[] = [];
  for (const part of parts) {
    const prev = merged[merged.length - 1];
    if (prev && prev.added === part.added && prev.removed === part.removed) {
      prev.value += part.value;
    } else {
      merged.push({ ...part });
    }
  }
  return merged;
}

function tokenColor(token: string, nextToken: string | undefined): keyof Theme | undefined {
  if (/^\s+$/.test(token)) return undefined;
  if (/^\/\/.*$/.test(token)) return "syntaxComment";
  if (/^(['"`]).*\1$/.test(token)) return "syntaxString";
  if (/^\d+(?:\.\d+)?$/.test(token)) return "syntaxNumber";
  if (KEYWORDS.has(token)) return "syntaxKeyword";
  if (/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(token) && nextToken === "(") {
    return "syntaxFunction";
  }
  return undefined;
}

function syntaxTokens(text: string): string[] {
  return (
    text.match(
      /(\/\/.*$|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\s+|[A-Za-z_$][A-Za-z0-9_$]*|\d+(?:\.\d+)?|.)/g,
    ) ?? [text]
  );
}

function SyntaxText({
  text,
  backgroundColor,
  dim,
}: {
  text: string;
  backgroundColor?: string;
  dim?: boolean;
}) {
  const tokens = syntaxTokens(text);
  return (
    <>
      {tokens.map((token, index) => {
        const colorToken = tokenColor(token, tokens[index + 1]);
        return (
          <Text
            key={index}
            color={colorToken ? themeColor(colorToken) : undefined}
            backgroundColor={backgroundColor}
            dimColor={dim}
          >
            {token}
          </Text>
        );
      })}
    </>
  );
}

function numberDiffLines(diff: LineObject[], startLine: number): NumberedDiffLine[] {
  let i = startLine;
  const result: NumberedDiffLine[] = [];
  const queue = [...diff];

  while (queue.length > 0) {
    const current = queue.shift();
    if (!current) continue;

    const line: NumberedDiffLine = { ...current, i };
    switch (current.type) {
      case "nochange":
      case "add":
        i++;
        result.push(line);
        break;
      case "remove": {
        result.push(line);
        let numRemoved = 0;
        while (queue[0]?.type === "remove") {
          i++;
          const removed = queue.shift();
          if (!removed) continue;
          result.push({ ...removed, i });
          numRemoved++;
        }
        i -= numRemoved;
        break;
      }
    }
  }

  return result;
}

function backgroundForType(type: LineType, dim: boolean): string | undefined {
  if (type === "add") return themeColor(dim ? "diffAddedDimmed" : "diffAdded");
  if (type === "remove") return themeColor(dim ? "diffRemovedDimmed" : "diffRemoved");
  return undefined;
}

function generateWordDiffElements(
  item: NumberedDiffLine,
  width: number,
  maxWidth: number,
  dim: boolean,
): ReactNode[] | null {
  const { type, i, wordDiff, matchedLine, originalCode } = item;
  if (!wordDiff || !matchedLine) return null;

  const removedLineText =
    type === "remove" ? originalCode : matchedLine.originalCode;
  const addedLineText =
    type === "remove" ? matchedLine.originalCode : originalCode;
  const wordDiffs = calculateWordDiffs(removedLineText, addedLineText);
  const totalLength = removedLineText.length + addedLineText.length;
  const changedLength = wordDiffs
    .filter((part) => part.added || part.removed)
    .reduce((sum, part) => sum + part.value.length, 0);

  if (totalLength === 0 || changedLength / totalLength > CHANGE_THRESHOLD || dim) {
    return null;
  }

  const sigil = type === "add" ? "+" : "-";
  const availableContentWidth = Math.max(1, width - maxWidth - 2);
  const wrappedLines: { content: ReactNode[]; contentWidth: number }[] = [];
  let currentLine: ReactNode[] = [];
  let currentLineWidth = 0;

  wordDiffs.forEach((part, partIndex) => {
    let shouldShow = false;
    let partBgColor: string | undefined;
    if (type === "add") {
      if (part.added) {
        shouldShow = true;
        partBgColor = themeColor("diffAddedWord");
      } else if (!part.removed) {
        shouldShow = true;
      }
    } else if (type === "remove") {
      if (part.removed) {
        shouldShow = true;
        partBgColor = themeColor("diffRemovedWord");
      } else if (!part.added) {
        shouldShow = true;
      }
    }
    if (!shouldShow) return;

    const partLines = wrapAnsi(part.value, availableContentWidth, {
      hard: true,
      wordWrap: true,
      trim: false,
    }).split("\n");
    partLines.forEach((partLine, lineIdx) => {
      if (lineIdx > 0 || currentLineWidth + stringWidth(partLine) > availableContentWidth) {
        if (currentLine.length > 0) {
          wrappedLines.push({
            content: [...currentLine],
            contentWidth: currentLineWidth,
          });
          currentLine = [];
          currentLineWidth = 0;
        }
      }
      currentLine.push(
        <Text
          key={`part-${partIndex}-${lineIdx}`}
          backgroundColor={partBgColor ?? backgroundForType(type, dim)}
          dimColor={dim}
        >
          <SyntaxText
            text={partLine}
            backgroundColor={partBgColor ?? backgroundForType(type, dim)}
            dim={dim}
          />
        </Text>,
      );
      currentLineWidth += stringWidth(partLine);
    });
  });

  if (currentLine.length > 0) {
    wrappedLines.push({ content: currentLine, contentWidth: currentLineWidth });
  }

  return wrappedLines.map(({ content, contentWidth }, lineIndex) => {
    const lineNum = lineIndex === 0 ? i : undefined;
    const lineNumStr =
      (lineNum !== undefined
        ? lineNum.toString().padStart(maxWidth)
        : " ".repeat(maxWidth)) + " ";
    const usedWidth = stringWidth(lineNumStr) + 1 + contentWidth;
    const padding = Math.max(0, width - usedWidth);
    const bgColor = backgroundForType(type, dim);

    return (
      <Box key={`${type}-${i}-${lineIndex}`} flexDirection="row">
        <Text color={themeColor("inactive")} backgroundColor={bgColor} dimColor>
          {lineNumStr}
          {sigil}
        </Text>
        <Text backgroundColor={bgColor} dimColor={dim}>
          {content}
          {" ".repeat(padding)}
        </Text>
      </Box>
    );
  });
}

function formatDiff(
  lines: string[],
  startingLineNumber: number,
  width: number,
  dim: boolean,
): ReactNode[] {
  const safeWidth = Math.max(1, Math.floor(width));
  const lineObjects = transformLinesToObjects(lines);
  const processedLines = processAdjacentLines(lineObjects);
  const numberedLines = numberDiffLines(processedLines, startingLineNumber);
  const maxLineNumber = Math.max(...numberedLines.map(({ i }) => i), 0);
  const maxWidth = Math.max(maxLineNumber.toString().length + 1, 0);

  return numberedLines.flatMap((item): ReactNode[] => {
    const { type, code, i, wordDiff, matchedLine } = item;

    if (wordDiff && matchedLine) {
      const wordDiffElements = generateWordDiffElements(
        item,
        safeWidth,
        maxWidth,
        dim,
      );
      if (wordDiffElements !== null) return wordDiffElements;
    }

    const availableContentWidth = Math.max(1, safeWidth - maxWidth - 2);
    const wrappedLines = wrapAnsi(code, availableContentWidth, {
      hard: true,
      wordWrap: true,
      trim: false,
    }).split("\n");

    return wrappedLines.map((line, lineIndex) => {
      const lineNum = lineIndex === 0 ? i : undefined;
      const lineNumStr =
        (lineNum !== undefined
          ? lineNum.toString().padStart(maxWidth)
          : " ".repeat(maxWidth)) + " ";
      const sigil = type === "add" ? "+" : type === "remove" ? "-" : " ";
      const contentWidth = stringWidth(lineNumStr) + 1 + stringWidth(line);
      const padding = Math.max(0, safeWidth - contentWidth);
      const bgColor = backgroundForType(type, dim);

      return (
        <Box key={`${type}-${i}-${lineIndex}`} flexDirection="row">
          <Text
            color={themeColor("inactive")}
            backgroundColor={bgColor}
            dimColor={dim || type === "nochange"}
          >
            {lineNumStr}
            {sigil}
          </Text>
          <Text backgroundColor={bgColor} dimColor={dim}>
            <SyntaxText text={line} backgroundColor={bgColor} dim={dim} />
            {" ".repeat(padding)}
          </Text>
        </Box>
      );
    });
  });
}

function HunkView({
  hunk,
  width,
  dim,
}: {
  hunk: StructuredPatchHunk;
  width: number;
  dim: boolean;
}) {
  const nodes = useMemo(
    () => formatDiff(hunk.lines, hunk.oldStart, width, dim),
    [hunk, width, dim],
  );

  return (
    <Box flexDirection="column">
      {nodes.map((node, index) => (
        <Box key={index}>{node}</Box>
      ))}
    </Box>
  );
}

export const DiffView = ({ diffLines, hunks, width }: DiffViewProps) => {
  const structuredHunks = hunks ?? (diffLines ? diffLinesToStructuredHunk(diffLines) : []);
  if (structuredHunks.length === 0) return null;

  const { additions, removals } = countChangedLines(structuredHunks);
  const displayWidth = Math.max(24, width ?? (process.stdout.columns ?? 80) - 8);
  const subtle = themeColor("subtle");

  return (
    <Box flexDirection="column" paddingLeft={3}>
      <Text color={themeColor("inactive")}>
        {additions > 0 ? (
          <>
            Added <Text bold>{additions}</Text> {additions === 1 ? "line" : "lines"}
          </>
        ) : null}
        {additions > 0 && removals > 0 ? ", " : null}
        {removals > 0 ? (
          <>
            {additions === 0 ? "Removed" : "removed"} <Text bold>{removals}</Text>{" "}
            {removals === 1 ? "line" : "lines"}
          </>
        ) : null}
      </Text>
      <Box
        flexDirection="column"
        borderStyle="single"
        borderColor={subtle}
        borderLeft={false}
        borderRight={false}
      >
        {structuredHunks.map((hunk, index) => (
          <Box key={`${hunk.oldStart}-${hunk.newStart}-${index}`} flexDirection="column">
            {index > 0 ? <Text color={subtle}>...</Text> : null}
            <HunkView hunk={hunk} width={displayWidth} dim={false} />
          </Box>
        ))}
      </Box>
    </Box>
  );
};
