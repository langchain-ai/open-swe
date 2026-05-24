// @ts-nocheck — ported from open-swe-app (Electron).
import { useState, useMemo, useEffect, useRef } from 'react';
import { diffLines } from 'diff';
import { getSingletonHighlighter, type ThemedToken } from 'shiki';
import type { DiffData } from '@/lib/agents/types';

interface DiffViewProps {
  diffData: DiffData;
}

// Map file extensions to shiki language IDs
function getLanguageFromPath(filePath: string): string {
  const ext = filePath.split('.').pop()?.toLowerCase() ?? '';
  const map: Record<string, string> = {
    ts: 'typescript', tsx: 'tsx', js: 'javascript', jsx: 'jsx',
    py: 'python', rb: 'ruby', rs: 'rust', go: 'go', java: 'java',
    kt: 'kotlin', swift: 'swift', c: 'c', cpp: 'cpp', cs: 'csharp',
    html: 'html', css: 'css', scss: 'scss', json: 'json',
    yaml: 'yaml', yml: 'yaml', toml: 'toml', md: 'markdown', mdx: 'mdx',
    sh: 'bash', bash: 'bash', zsh: 'bash', fish: 'fish', sql: 'sql',
    xml: 'xml', php: 'php', r: 'r', lua: 'lua', ex: 'elixir',
    exs: 'elixir', elm: 'elm', clj: 'clojure', hs: 'haskell',
    scala: 'scala', vue: 'vue', svelte: 'svelte', graphql: 'graphql',
    tf: 'hcl', hcl: 'hcl', ini: 'ini',
  };
  const filename = filePath.split('/').pop()?.toLowerCase() ?? '';
  if (filename === 'dockerfile') return 'dockerfile';
  if (filename === 'makefile') return 'makefile';
  return map[ext] ?? 'text';
}

// Highlighted line cache: maps "lang::lineText" -> ThemedToken[]
type TokenCache = Map<string, ThemedToken[]>;

// Renders a line of text as highlighted token spans, falling back to plain text
function HighlightedLine({ tokens, fallback, isAdd, isRemove }: {
  tokens: ThemedToken[] | null;
  fallback: string;
  isAdd: boolean;
  isRemove: boolean;
}) {
  if (!tokens) {
    return (
      <span className={isAdd ? 'text-[#a5d6a7]' : isRemove ? 'text-[#ffb4ab]' : 'text-gray-400'}>
        {fallback}
      </span>
    );
  }
  return (
    <>
      {tokens.map((token, i) => (
        <span key={i} style={{ color: token.color }}>
          {token.content}
        </span>
      ))}
    </>
  );
}

const CONTEXT_LINES = 3;
const MAX_COLLAPSED_LINES = 20;

type DiffLineData = {
  type: 'context' | 'remove' | 'add' | 'separator';
  text: string;
  oldLineNum?: number;
  newLineNum?: number;
};

function toLineArray(text: string): string[] {
  if (text.length === 0) return [];
  const lines = text.split('\n');
  if (text.endsWith('\n')) lines.pop();
  return lines;
}

function computeDiffLines(
  originalContent: string | null,
  newContent: string
): DiffLineData[] {
  const result: DiffLineData[] = [];
  const parts = diffLines(originalContent ?? '', newContent, {
    ignoreWhitespace: false,
    newlineIsToken: false,
  });

  let oldLineNum = 1;
  let newLineNum = 1;

  for (const part of parts) {
    const lines = toLineArray(part.value);

    if (part.added) {
      for (const line of lines) {
        result.push({
          type: 'add',
          text: line,
          newLineNum,
        });
        newLineNum += 1;
      }
      continue;
    }

    if (part.removed) {
      for (const line of lines) {
        result.push({
          type: 'remove',
          text: line,
          oldLineNum,
        });
        oldLineNum += 1;
      }
      continue;
    }

    for (const line of lines) {
      result.push({
        type: 'context',
        text: line,
        oldLineNum,
        newLineNum,
      });
      oldLineNum += 1;
      newLineNum += 1;
    }
  }

  return result;
}

function filterToHunks(lines: DiffLineData[], contextLines: number = CONTEXT_LINES): DiffLineData[] {
  const changeIndices: number[] = [];
  lines.forEach((line, idx) => {
    if (line.type === 'add' || line.type === 'remove') {
      changeIndices.push(idx);
    }
  });

  if (changeIndices.length === 0) {
    return [];
  }

  const includeSet = new Set<number>();
  for (const idx of changeIndices) {
    for (let i = Math.max(0, idx - contextLines); i <= Math.min(lines.length - 1, idx + contextLines); i++) {
      includeSet.add(i);
    }
  }

  const result: DiffLineData[] = [];
  let lastIncluded = -2;

  for (let i = 0; i < lines.length; i++) {
    if (includeSet.has(i)) {
      if (lastIncluded >= 0 && i - lastIncluded > 1) {
        result.push({ type: 'separator', text: '···' });
      }
      result.push(lines[i]);
      lastIncluded = i;
    }
  }

  return result;
}

export function DiffView({ diffData }: DiffViewProps) {
  const [expanded, setExpanded] = useState(false);
  const [tokenCache, setTokenCache] = useState<TokenCache | null>(null);
  const highlightingRef = useRef(false);

  const { originalContent, newContent, filePath, isNewFile, isBinary } = diffData;
  const language = getLanguageFromPath(filePath);

  const allDiffLines = useMemo(
    () => computeDiffLines(originalContent, newContent),
    [originalContent, newContent]
  );

  const hunkLines = useMemo(
    () => filterToHunks(allDiffLines),
    [allDiffLines]
  );

  const stats = useMemo(() => {
    let additions = 0;
    let deletions = 0;
    for (const line of allDiffLines) {
      if (line.type === 'add') additions++;
      else if (line.type === 'remove') deletions++;
    }
    return { additions, deletions };
  }, [allDiffLines]);

  // Tokenize all visible lines for syntax highlighting
  useEffect(() => {
    if (language === 'text' || isBinary) return;
    if (highlightingRef.current) return;
    highlightingRef.current = true;

    const linesToHighlight = hunkLines.filter(l => l.type !== 'separator');

    getSingletonHighlighter({
      themes: ['github-dark'],
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      langs: [language as any],
    }).then(highlighter => {
      const cache: TokenCache = new Map();
      for (const line of linesToHighlight) {
        const cacheKey = `${language}::${line.text}`;
        if (cache.has(cacheKey)) continue;
        try {
          const result = highlighter.codeToTokens(line.text, {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            lang: language as any,
            theme: 'github-dark',
          });
          cache.set(cacheKey, result.tokens[0] ?? []);
        } catch {
          // Skip lines that fail tokenization
        }
      }
      setTokenCache(cache);
      highlightingRef.current = false;
    }).catch((err: unknown) => {
      console.warn('[diff-view] Syntax highlighting failed:', err);
      highlightingRef.current = false;
    });
  }, [hunkLines, language, isBinary]);

  if (isBinary) {
    return (
      <div className="mt-2 text-gray-500 text-xs font-mono">
        Binary file - diff not available
      </div>
    );
  }

  const displayLines = expanded ? hunkLines : hunkLines.slice(0, MAX_COLLAPSED_LINES);
  const hasMoreLines = hunkLines.length > MAX_COLLAPSED_LINES;
  const hiddenCount = hunkLines.length - MAX_COLLAPSED_LINES;

  if (hunkLines.length === 0) {
    return (
      <div className="mt-2 text-gray-500 text-xs font-mono">
        No changes
      </div>
    );
  }

  return (
    <div className="mt-2 font-mono text-xs">
      <div className="flex items-center gap-2 text-gray-500 mb-1">
        <span className="text-gray-400">{filePath.split('/').pop()}</span>
        {isNewFile && <span>(new)</span>}
        <span className="text-green-400">+{stats.additions}</span>
        <span className="text-red-400">-{stats.deletions}</span>
      </div>

      <div className="max-h-60 overflow-auto border-l border-gray-700 pl-2">
        {displayLines.map((line, idx) => {
          if (line.type === 'separator') {
            return (
              <div key={idx} className="text-gray-600 py-0.5">
                {line.text}
              </div>
            );
          }

          const isAdd = line.type === 'add';
          const isRemove = line.type === 'remove';
          const cacheKey = `${language}::${line.text}`;
          const tokens = tokenCache?.get(cacheKey) ?? null;

          return (
            <div
              key={idx}
              className={`whitespace-pre ${
                isAdd ? 'bg-[#12261a]' :
                isRemove ? 'bg-[#2d1a1f]' : ''
              }`}
            >
              <span className="text-gray-600 w-8 inline-block text-right pr-2">
                {line.oldLineNum || line.newLineNum || ''}
              </span>
              <span className={`w-4 inline-block ${
                isAdd ? 'text-[#3fb950]' :
                isRemove ? 'text-[#f85149]' : 'text-gray-600'
              }`}>
                {isAdd ? '+' : isRemove ? '-' : ' '}
              </span>
              <HighlightedLine tokens={tokens} fallback={line.text} isAdd={isAdd} isRemove={isRemove} />
            </div>
          );
        })}
      </div>

      {hasMoreLines && !expanded && (
        <button
          onClick={() => setExpanded(true)}
          className="mt-1 text-xs text-[#87CEEB] hover:text-[#a8d8ea]"
        >
          +{hiddenCount} more lines
        </button>
      )}
      {expanded && hasMoreLines && (
        <button
          onClick={() => setExpanded(false)}
          className="mt-1 text-xs text-[#87CEEB] hover:text-[#a8d8ea]"
        >
          Show less
        </button>
      )}
    </div>
  );
}
