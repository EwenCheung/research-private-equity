// Mirrors contracts/chart_spec.schema.json (docs/contracts.md section 4).
export type Freshness = "fresh" | "aging" | "stale" | "never";
export type Format = "int" | "float" | "pct" | "usd" | "usd_compact" | "multiple" | "date" | "text" | "url";
export type Kind = "line" | "area" | "bar" | "stacked_bar" | "scatter" | "table" | "stat" | "timeline";

export interface Field {
  field: string;
  type: "temporal" | "quantitative" | "nominal" | "ordinal";
  label: string;
  format?: Format;
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
