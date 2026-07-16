interface HeaderBarProps {
  compact?: boolean;
}

export function HeaderBar({ compact }: HeaderBarProps) {
  if (compact) {
    return (
      <div className="flex items-center gap-3 px-3 py-2 font-sans text-xs border-b border-gray-700/50 shrink-0">
        <pre className="text-[#e07a5f] leading-none text-[10px] m-0">{`╭─╮
│◠│
╰─╯`}</pre>
        <span className="text-gray-300 font-medium">Jarvis Desktop App</span>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-4 px-4 py-3 font-sans text-sm">
      <pre className="text-[#e07a5f] leading-none text-xs">
{` ╭───╮
 │ ◠ │
 ╰───╯`}
      </pre>
      <span className="text-gray-200 font-semibold">Jarvis Desktop App v0.1.0</span>
    </div>
  );
}
