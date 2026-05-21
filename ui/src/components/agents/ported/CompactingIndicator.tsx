import { LoaderCircle } from 'lucide-react';
import { useState, useEffect } from 'react';

export function CompactingIndicator() {
  const [dots, setDots] = useState(1);

  useEffect(() => {
    const interval = setInterval(() => {
      setDots((d) => (d % 3) + 1);
    }, 400);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="flex items-center gap-2 text-purple-400 text-xs font-sans">
      <LoaderCircle size={12} strokeWidth={1.5} className="animate-spin" />
      <span>
        Compacting conversation{'.'.repeat(dots)}
      </span>
    </div>
  );
}
