const numberFormat = new Intl.NumberFormat("en-US");
const currencyFormat = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

export const formatNumber = (n: number) => numberFormat.format(n);
export const formatCurrency = (n: number) => currencyFormat.format(n);

/** ratio (0-1) -> "75%"; null means "no data yet". */
export const formatPercent = (ratio: number | null) =>
  ratio === null ? "—" : `${Math.round(ratio * 100)}%`;

export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString();
}

/** part/whole as a whole-number percent, "—" when there is nothing to divide by. */
export const formatShare = (part: number, whole: number) =>
  whole > 0 ? `${Math.round((part / whole) * 100)}%` : "—";

export const humanize = (value: string) => value.replace(/_/g, " ");
const compactCurrencyFormat = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

/** 284000 -> "$284K" */
export const formatCompact = (n: number) => compactCurrencyFormat.format(n);