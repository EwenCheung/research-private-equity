import type { Format } from "./types";

type Cell = string | number | null | undefined;

const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const int = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const float = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

/** Format a cell for display. Nothing is rounded beyond what the format names. */
export function fmt(value: Cell, format: Format = "text"): string {
  if (value === null || value === undefined || value === "") return "–";
  if (format === "text" || format === "date" || format === "url") return String(value);
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  switch (format) {
    case "int":
      return int.format(n);
    case "float":
      return float.format(n);
    case "pct":
      return `${float.format(n * 100)}%`;
    case "usd":
      return `$${int.format(n)}`;
    case "usd_compact":
      return `$${compact.format(n)}`;
    case "multiple":
      return `${float.format(n)}x`;
  }
}

export const isNumeric = (f: Format) => ["int", "float", "pct", "usd", "usd_compact", "multiple"].includes(f);

const DATE = /^\d{4}-\d{2}-\d{2}$/;
export const toDate = (v: Cell) => (typeof v === "string" && DATE.test(v) ? new Date(`${v}T00:00:00Z`) : v);

export const shortDate = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });

/** 2026-10-05T06:02:11Z -> "2026-10-05 06:02 UTC" */
export const utc = (iso: string) => `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;

export const csvCell = (v: Cell) => {
  const s = v === null || v === undefined ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};
