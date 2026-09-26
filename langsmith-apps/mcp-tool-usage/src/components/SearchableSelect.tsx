import { useEffect, useRef, useState } from 'react';
import { Select } from '@langchain/macaw-components/Select';
import { cn } from '@langchain/macaw-components/utils/cn';

const PAGE_SIZE = 25;

export interface SearchableSelectItem {
  id: string;
  name: string;
}

interface Props<T extends SearchableSelectItem> {
  id?: string;
  value: string;
  valueLabel?: string;
  onSelect: (item: T) => void;
  fetchPage: (search: string, offset: number, limit: number) => Promise<T[]>;
  placeholder: string;
  searchPlaceholder?: string;
  emptyLabel?: string;
  disabled?: boolean;
  className?: string;
}

// Only data fetching lives here; Macaw owns the combobox and its interactions.
export function SearchableSelect<T extends SearchableSelectItem>({
  id,
  value,
  valueLabel,
  onSelect,
  fetchPage,
  placeholder,
  searchPlaceholder = 'Search by name…',
  emptyLabel = 'No results',
  disabled,
  className,
}: Props<T>) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [items, setItems] = useState<T[]>([]);
  const [selected, setSelected] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState(false);
  const request = useRef(0);
  const pending = useRef(false);

  useEffect(() => {
    if (!open) return;
    const current = ++request.current;
    pending.current = true;
    setLoading(true);
    setError(false);
    setItems([]);
    setHasMore(false);
    fetchPage(search, 0, PAGE_SIZE)
      .then((page) => {
        if (current !== request.current) return;
        setItems(page);
        setHasMore(page.length === PAGE_SIZE);
      })
      .catch(() => {
        if (current === request.current) setError(true);
      })
      .finally(() => {
        if (current !== request.current) return;
        pending.current = false;
        setLoading(false);
      });
    return () => {
      request.current++;
    };
  }, [open, search, fetchPage]);

  async function loadMore() {
    if (pending.current || !hasMore) return;
    const current = request.current;
    pending.current = true;
    setLoading(true);
    try {
      const page = await fetchPage(search, items.length, PAGE_SIZE);
      if (current !== request.current) return;
      setItems((previous) => {
        const seen = new Set(previous.map((item) => item.id));
        return [...previous, ...page.filter((item) => !seen.has(item.id))];
      });
      setHasMore(page.length === PAGE_SIZE);
    } catch {
      if (current === request.current) setHasMore(false);
    } finally {
      if (current === request.current) {
        pending.current = false;
        setLoading(false);
      }
    }
  }

  return (
    <div id={id} className={cn('min-w-0 max-w-[420px] flex-1', className)}>
      <Select
        aria-label={placeholder}
        value={value || undefined}
        options={items.map((item) => ({ value: item.id, label: item.name }))}
        onChange={(next) => {
          const item = items.find((item) => item.id === next);
          if (item) {
            setSelected(item);
            onSelect(item);
          }
        }}
        open={open}
        onOpenChange={(next) => {
          if (next) setSearch('');
          setOpen(next);
        }}
        onSearchChange={setSearch}
        onEndReached={loadMore}
        hideSearch={false}
        disableFilter
        loading={loading}
        placeholder={selected?.id === value ? selected.name : valueLabel || value || placeholder}
        searchPlaceholder={searchPlaceholder}
        emptyText={error ? 'Could not load options. Close and reopen to retry.' : emptyLabel}
        disabled={disabled}
        triggerClassName="w-full"
      />
    </div>
  );
}
