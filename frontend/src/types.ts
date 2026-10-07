// Mirrors contracts/chart_spec.schema.json (docs/contracts.md section 4).
export type Freshness = "fresh" | "aging" | "stale" | "never";
export type Format = "int" | "float" | "pct" | "usd" | "usd_compact" | "multiple" | "date" | "text" | "url";
export type Kind = "line" | "area" | "bar" | "stacked_bar" | "scatter" | "table" | "stat" | "timeline" | "combo";

export interface Field {
  field: string;
  type: "temporal" | "quantitative" | "nominal" | "ordinal";
  label: string;
  format?: Format;
}

/** One mark of a combined chart. bar, line and point draw y; band shades y_low to y_high; rule draws a vertical line captioned by label.
 *  A layer draws the rows whose value field is not null. series splits it into one colour per value, otherwise name is its legend entry. */
export interface Layer {
  mark: "bar" | "line" | "point" | "band" | "rule";
  name: string;
  y?: string;
  y_low?: string;
  y_high?: string;
  label?: string;
  series?: string;
  panel?: number;
}

export interface Column {
  field: string;
  label: string;
  format: Format;
}

export interface SourceLine {
  source: string;
  label: string;
  url: string;
  method: "api" | "scrape" | "manual" | "ledger";
  tier: string;
  cadence: string;
  as_of: string | null;
  retrieved_at: string | null;
  freshness: Freshness;
  manual: { entered_by: string; entered_at: string; evidence: string } | null;
}

export interface ChartSpec {
  id: string;
  page: string;
  title: string;
  subtitle: string;
  kind: Kind;
  layers?: Layer[];
  panels?: { label: string; format?: Format }[];
  encoding: { x?: Field; y?: Field; color?: Field; facet?: Field };
  columns: Column[];
  rows: Record<string, string | number | null>[];
  takeaway: string[];
  assumptions: string[];
  badges: ("arithmetic" | "extrapolation")[];
  as_of: string | null;
  sources: SourceLine[];
  status: "ok" | "awaiting_data";
  generated_at: string;
}
