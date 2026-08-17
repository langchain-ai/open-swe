export function parseApprovalThreshold(value: string): number | null {
  if (!value.trim()) return null
  const threshold = Number(value)
  return Number.isInteger(threshold) && threshold >= 0 && threshold <= 100
    ? threshold
    : null
}
