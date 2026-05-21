// @ts-nocheck — ported from open-swe-app (Electron).
import { Children, isValidElement, type ReactNode } from 'react';

interface MarkdownTableProps {
  children: ReactNode;
}

function countCells(node: ReactNode): number {
  return Children.toArray(node).filter((child) => isValidElement(child) && (child.type === 'th' || child.type === 'td')).length;
}

function collectColumnCounts(node: ReactNode, counts: number[]) {
  Children.forEach(node, (child) => {
    if (!isValidElement(child)) return;

    if (child.type === 'tr') {
      const count = countCells(child.props.children);
      if (count > 0) counts.push(count);
      return;
    }

    collectColumnCounts(child.props.children, counts);
  });
}

function getTableMinWidth(columnCount: number): string {
  if (columnCount <= 1) return '100%';
  return `max(100%, ${Math.max(420, columnCount * 160)}px)`;
}

export function MarkdownTable({ children }: MarkdownTableProps) {
  const columnCounts: number[] = [];
  collectColumnCounts(children, columnCounts);
  const columnCount = Math.max(0, ...columnCounts);

  return (
    <div className="my-2 max-w-full overflow-x-auto pb-1 text-[color:var(--ui-text-muted)]">
      <table className="w-max border-collapse align-top" style={{ minWidth: getTableMinWidth(columnCount) }}>
        {children}
      </table>
    </div>
  );
}
