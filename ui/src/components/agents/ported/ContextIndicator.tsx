import { useState, useRef, useEffect } from 'react';
import { useStore } from '../../store';
import { COMPACT_THRESHOLD } from '../../context-limits';

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

interface ContextIndicatorProps {
  sessionId: string;
}

export function ContextIndicator({ sessionId }: ContextIndicatorProps) {
  const contextWindow = useStore(state => state.sessions[sessionId]?.contextWindow);
  const hasMessages = useStore(state => (state.sessions[sessionId]?.messages.length ?? 0) > 0);
  const harness = useStore(state => state.harness);
  const [showTooltip, setShowTooltip] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const usedTokens = contextWindow?.used ?? 0;
  const contextLimit = contextWindow?.size ?? 0;
  const showUnavailable = !contextWindow && hasMessages;

  const fraction = contextLimit > 0 ? Math.min(usedTokens / contextLimit, 1) : 0;
  const percentage = Math.round(fraction * 100);
  const isNearLimit = fraction >= COMPACT_THRESHOLD;

  const size = 14;
  const strokeWidth = 1.5;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const dashOffset = circumference * (1 - fraction);

  let strokeColor = '#6b7280'; // gray-500
  if (fraction > 0) strokeColor = '#9ca3af'; // gray-400
  if (fraction >= 0.5) strokeColor = '#93c5fd'; // blue-300
  if (fraction >= 0.75) strokeColor = '#fbbf24'; // amber-400
  if (fraction >= COMPACT_THRESHOLD) strokeColor = '#f87171'; // red-400

  useEffect(() => {
    if (!showTooltip) return;
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setShowTooltip(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [showTooltip]);

  if (!contextWindow && !hasMessages) return null;

  if (showUnavailable) {
    return (
      <div
        ref={containerRef}
        className="relative flex items-center"
        onMouseEnter={() => setShowTooltip(true)}
        onMouseLeave={() => setShowTooltip(false)}
      >
        <div className="cursor-default flex items-center gap-1 opacity-40">
          <svg width={size} height={size} className="block">
            <circle
              cx={size / 2}
              cy={size / 2}
              r={radius}
              fill="none"
              stroke="#4b5563"
              strokeWidth={strokeWidth}
              strokeDasharray="2 2"
            />
          </svg>
        </div>

        {showTooltip && (
          <div className="absolute bottom-full mb-2 right-0 bg-[#1a1f2e] border border-[#2a3142] rounded-lg shadow-xl px-3 py-2 z-50 whitespace-nowrap text-xs font-sans">
            <div className="text-gray-400">
              Context usage unavailable
            </div>
            <div className="text-gray-500 text-[10px] mt-1">
              {harness === 'cursor' ? 'Cursor' : harness === 'codex' ? 'Codex' : 'This provider'} doesn't report token usage
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="relative flex items-center"
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
    >
      <div className="cursor-default flex items-center gap-1">
        <svg width={size} height={size} className="block -rotate-90">
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="#374151"
            strokeWidth={strokeWidth}
          />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={strokeColor}
            strokeWidth={strokeWidth}
            strokeDasharray={circumference}
            strokeDashoffset={dashOffset}
            strokeLinecap="round"
          />
        </svg>
      </div>

      {showTooltip && (
        <div className="absolute bottom-full mb-2 right-0 bg-[#1a1f2e] border border-[#2a3142] rounded-lg shadow-xl px-3 py-2 z-50 whitespace-nowrap text-xs font-sans">
          <div className="text-gray-300 font-medium">
            Context: {percentage}% — {formatTokens(usedTokens)} / {formatTokens(contextLimit)} tokens
          </div>
          {isNearLimit && (
            <div className="text-amber-400 text-[10px] mt-1">
              Approaching context limit
            </div>
          )}
        </div>
      )}
    </div>
  );
}
