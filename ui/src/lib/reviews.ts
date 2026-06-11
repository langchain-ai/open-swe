export function prSizeLabel(additions: number, deletions: number): string {
  const total = additions + deletions
  if (total <= 50) return "XS"
  if (total <= 200) return "S"
  if (total <= 600) return "M"
  if (total <= 1500) return "L"
  return "XL"
}
