import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react';

interface PanelResizeHandleProps {
  onDragStart?: () => void;
  onDragMove: (deltaXFromPointerDown: number) => void;
  onDoubleClick?: () => void;
  /** Which edge of the hit area the visible line sits on (avoids a centered gap next to panels). */
  lineAlign?: 'start' | 'end';
  className?: string;
}

export function PanelResizeHandle({
  onDragStart,
  onDragMove,
  onDoubleClick,
  lineAlign = 'start',
  className = '',
}: PanelResizeHandleProps) {
  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    onDragStart?.();
    const el = e.currentTarget;
    const pointerId = e.pointerId;
    el.setPointerCapture(pointerId);
    const startX = e.clientX;
    const move = (ev: PointerEvent) => {
      if (ev.pointerId !== pointerId) return;
      onDragMove(ev.clientX - startX);
    };
    const cleanup = (ev: PointerEvent) => {
      if (ev.pointerId !== pointerId) return;
      try {
        el.releasePointerCapture(pointerId);
      } catch {
        /* already released */
      }
      el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerup', cleanup);
      el.removeEventListener('pointercancel', cleanup);
    };
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', cleanup);
    el.addEventListener('pointercancel', cleanup);
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      onPointerDown={onPointerDown}
      onDoubleClick={onDoubleClick}
      style={{ WebkitAppRegion: 'no-drag' } as CSSProperties}
      className={`group shrink-0 w-1 cursor-col-resize flex box-border ${
        lineAlign === 'end' ? 'justify-end' : 'justify-start'
      } ${className}`}
    >
      <div className="w-px h-full bg-gray-800 group-hover:bg-[var(--ui-accent)]" />
    </div>
  );
}
