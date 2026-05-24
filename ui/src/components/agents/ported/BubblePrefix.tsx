import type { Author } from '@/lib/agents/types';

interface BubblePrefixProps {
  author: Author;
}

export function BubblePrefix({ author }: BubblePrefixProps) {
  switch (author) {
    case 'user':
      return <span className="text-[#87CEEB] font-bold mr-2">&gt;</span>;
    case 'agent':
      return <span className="text-green-400 font-bold mr-2">✦</span>;
    case 'tool':
      return <span className="text-yellow-400 font-bold mr-2">⚡</span>;
    case 'system':
      return <span className="text-purple-400 font-bold mr-2">ℹ</span>;
  }
}
