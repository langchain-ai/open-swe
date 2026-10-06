import type { components } from "../lib/schema-lite";

type Schemas = components["schemas"];

/** The server-owned data scope every cross-record list accepts. */
export type DataScope = Schemas["DataScope"];

export const DATA_SCOPE_PARAM = "scope";
export const DEFAULT_DATA_SCOPE: DataScope = "MY";

/** Accessible name for the My / All tablist inside the Filters popover. */
export const ACCOUNT_SCOPE_TABS_LABEL = "My or all accounts";

export const ACCOUNT_SCOPE_TAB_ITEMS: readonly {
  value: DataScope;
  label: string;
}[] = [
  { value: "MY", label: "My accounts" },
  { value: "ALL", label: "All accounts" },
];

/** Bare and malformed page URLs fail closed to the caller's own rows. */
export function parseDataScope(raw: string | null): DataScope {
  return raw === "ALL" ? "ALL" : DEFAULT_DATA_SCOPE;
}

/** MY is the bare canonical URL; ALL is always explicit and shareable. */
export function setDataScope(
  search: URLSearchParams,
  scope: DataScope
): URLSearchParams {
  const next = new URLSearchParams(search.toString());
  if (scope === DEFAULT_DATA_SCOPE) next.delete(DATA_SCOPE_PARAM);
  else next.set(DATA_SCOPE_PARAM, scope);
  next.delete("cursor");
  return next;
}

/** The Filters popover shows MY as applied and absence as the global catalog. */
export function dataScopeFilterValue(scope: DataScope): string | undefined {
  return scope === "MY" ? "MY" : undefined;
}

/** Clearing the MY pill is the explicit transition to the global catalog. */
export function dataScopeFromFilter(value: string | undefined): DataScope {
  return value === "MY" ? "MY" : "ALL";
}

/** A Filters popover tab writes one of the two locked scopes. */
export function dataScopeFromTab(value: string): DataScope {
  return value === "ALL" ? "ALL" : DEFAULT_DATA_SCOPE;
}
