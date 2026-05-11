import { Box, Text } from 'ink';
import { themeColor } from '@tui/theme.js';

type Props = {
  children: string;
};

type InlineToken =
  | { kind: 'text'; value: string }
  | { kind: 'code'; value: string }
  | { kind: 'bold'; value: string }
  | { kind: 'italic'; value: string }
  | { kind: 'link'; label: string; url: string };

const TOKEN_REGEX = /(`[^`\n]+`)|(\*\*[^\n*]+\*\*)|(\*[^\n*]+\*)|(_[^\n_]+_)|(\[[^\]\n]+\]\([^)\n]+\))/g;

function tokenizeInline(text: string): InlineToken[] {
  const tokens: InlineToken[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  TOKEN_REGEX.lastIndex = 0;
  while ((match = TOKEN_REGEX.exec(text)) !== null) {
    if (match.index > lastIndex) {
      tokens.push({ kind: 'text', value: text.slice(lastIndex, match.index) });
    }
    const matched = match[0];
    if (matched.startsWith('`')) {
      tokens.push({ kind: 'code', value: matched.slice(1, -1) });
    } else if (matched.startsWith('**')) {
      tokens.push({ kind: 'bold', value: matched.slice(2, -2) });
    } else if (matched.startsWith('*')) {
      tokens.push({ kind: 'italic', value: matched.slice(1, -1) });
    } else if (matched.startsWith('_')) {
      tokens.push({ kind: 'italic', value: matched.slice(1, -1) });
    } else if (matched.startsWith('[')) {
      const closeBracket = matched.indexOf(']');
      const openParen = matched.indexOf('(', closeBracket);
      const label = matched.slice(1, closeBracket);
      const url = matched.slice(openParen + 1, -1);
      tokens.push({ kind: 'link', label, url });
    }
    lastIndex = match.index + matched.length;
  }
  if (lastIndex < text.length) {
    tokens.push({ kind: 'text', value: text.slice(lastIndex) });
  }
  return tokens;
}

const InlineRenderer = ({ text }: { text: string }) => {
  const tokens = tokenizeInline(text);
  const subtle = themeColor('subtle');
  const suggestion = themeColor('suggestion');
  return (
    <>
      {tokens.map((token, idx) => {
        switch (token.kind) {
          case 'text':
            return <Text key={idx}>{token.value}</Text>;
          case 'code':
            return (
              <Text key={idx} color={themeColor('warning')}>
                {token.value}
              </Text>
            );
          case 'bold':
            return (
              <Text key={idx} bold>
                {token.value}
              </Text>
            );
          case 'italic':
            return (
              <Text key={idx} italic>
                {token.value}
              </Text>
            );
          case 'link':
            return (
              <Text key={idx}>
                <Text color={suggestion} underline>
                  {token.label}
                </Text>
                <Text color={subtle}> ({token.url})</Text>
              </Text>
            );
        }
      })}
    </>
  );
};

type Block =
  | { kind: 'paragraph'; lines: string[] }
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'codeblock'; lang: string; lines: string[] }
  | { kind: 'list'; items: string[]; ordered: boolean }
  | { kind: 'blockquote'; lines: string[] }
  | { kind: 'rule' };

function parseBlocks(source: string): Block[] {
  const lines = source.split('\n');
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const raw = lines[i] ?? '';
    const line = raw.trimEnd();

    if (!line.trim()) {
      i++;
      continue;
    }

    if (line.trim() === '---' || line.trim() === '***') {
      blocks.push({ kind: 'rule' });
      i++;
      continue;
    }

    const fenceMatch = line.match(/^```(\w*)\s*$/);
    if (fenceMatch) {
      const lang = fenceMatch[1] ?? '';
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !(lines[i] ?? '').match(/^```\s*$/)) {
        codeLines.push(lines[i] ?? '');
        i++;
      }
      i++;
      blocks.push({ kind: 'codeblock', lang, lines: codeLines });
      continue;
    }

    const headingMatch = line.match(/^(#{1,6})\s+(.*)$/);
    if (headingMatch) {
      blocks.push({
        kind: 'heading',
        level: headingMatch[1].length,
        text: headingMatch[2],
      });
      i++;
      continue;
    }

    const bulletMatch = line.match(/^\s*[-*+]\s+(.*)$/);
    const orderedMatch = line.match(/^\s*\d+\.\s+(.*)$/);
    if (bulletMatch || orderedMatch) {
      const items: string[] = [];
      const ordered = !!orderedMatch;
      while (i < lines.length) {
        const cur = (lines[i] ?? '').trimEnd();
        if (!cur.trim()) break;
        const m = ordered ? cur.match(/^\s*\d+\.\s+(.*)$/) : cur.match(/^\s*[-*+]\s+(.*)$/);
        if (!m) break;
        items.push(m[1]);
        i++;
      }
      blocks.push({ kind: 'list', items, ordered });
      continue;
    }

    if (line.startsWith('>')) {
      const quoteLines: string[] = [];
      while (i < lines.length && (lines[i] ?? '').trimStart().startsWith('>')) {
        quoteLines.push((lines[i] ?? '').replace(/^\s*>\s?/, ''));
        i++;
      }
      blocks.push({ kind: 'blockquote', lines: quoteLines });
      continue;
    }

    const paraLines: string[] = [];
    while (i < lines.length) {
      const cur = (lines[i] ?? '').trimEnd();
      if (!cur.trim()) break;
      if (cur.match(/^```/) || cur.match(/^#{1,6}\s/) || cur.startsWith('>')) break;
      if (cur.match(/^\s*[-*+]\s/) || cur.match(/^\s*\d+\.\s/)) break;
      paraLines.push(cur);
      i++;
    }
    blocks.push({ kind: 'paragraph', lines: paraLines });
  }
  return blocks;
}

export const Markdown = ({ children }: Props) => {
  const blocks = parseBlocks(children);
  const subtle = themeColor('subtle');
  const inactive = themeColor('inactive');
  const brand = themeColor('brand');

  return (
    <Box flexDirection="column">
      {blocks.map((block, idx) => {
        switch (block.kind) {
          case 'paragraph':
            return (
              <Box key={idx} flexDirection="column">
                {block.lines.map((line, lineIdx) => (
                  <Text key={lineIdx}>
                    <InlineRenderer text={line} />
                  </Text>
                ))}
              </Box>
            );
          case 'heading':
            return (
              <Box key={idx} marginTop={idx === 0 ? 0 : 1}>
                <Text bold color={block.level <= 2 ? brand : undefined}>
                  <InlineRenderer text={block.text} />
                </Text>
              </Box>
            );
          case 'codeblock':
            return (
              <Box
                key={idx}
                flexDirection="column"
                borderStyle="round"
                borderColor={inactive}
                paddingX={1}
                marginY={0}
              >
                {block.lang ? (
                  <Text color={subtle} dimColor>
                    {block.lang}
                  </Text>
                ) : null}
                {block.lines.map((line, lineIdx) => (
                  <Text key={lineIdx}>{line || ' '}</Text>
                ))}
              </Box>
            );
          case 'list':
            return (
              <Box key={idx} flexDirection="column">
                {block.items.map((item, itemIdx) => (
                  <Box key={itemIdx}>
                    <Text color={brand}>{block.ordered ? `${itemIdx + 1}.` : '•'} </Text>
                    <Text>
                      <InlineRenderer text={item} />
                    </Text>
                  </Box>
                ))}
              </Box>
            );
          case 'blockquote':
            return (
              <Box key={idx} flexDirection="column" paddingLeft={1}>
                {block.lines.map((line, lineIdx) => (
                  <Box key={lineIdx}>
                    <Text color={inactive}>▎ </Text>
                    <Text color={inactive} italic>
                      <InlineRenderer text={line} />
                    </Text>
                  </Box>
                ))}
              </Box>
            );
          case 'rule':
            return (
              <Box key={idx}>
                <Text color={subtle}>─────────────</Text>
              </Box>
            );
        }
      })}
    </Box>
  );
};
