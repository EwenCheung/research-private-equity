import * as Plot from "@observablehq/plot";
import { useContext, useEffect, useRef, useState } from "react";
import { axis, fmt, shortDate, toDate } from "../format";
import { SchemeContext } from "../theme";
import type { ChartSpec } from "../types";

type Row = ChartSpec["rows"][number];
const SLOTS = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7", "--s8"];

/** Series in order of first appearance, so a colour follows its entity and never its rank. */
export function seriesOf(spec: ChartSpec): string[] {
  const f = spec.encoding.color?.field;
  return f ? [...new Set(spec.rows.map((r) => String(r[f])))] : [];
}

export function Legend({ series, colors }: { series: string[]; colors: string[] }) {
  if (series.length < 2) return null;
  return (
    <div className="legend">
      {series.map((s, i) => (
        <span key={s}>
          <i style={{ background: colors[i] }} />
          {s}
        </span>
      ))}
    </div>
  );
}

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  return { ref, width };
}

/** Each company owns one palette slot (index into SLOTS), so it is the same colour on every chart:
 *  Anthropic orange, OpenAI dark green, Google blue, the rest anything distinct.
 *  Incident severities read as a heat scale: amber, orange, red. */
const NAME_SLOT: Record<string, number> = {
  Anthropic: 1, // --s2 orange
  OpenAI: 5, // --s6 dark green
  "Google DeepMind": 0, // --s1 blue
  xAI: 6, // --s7 purple
  "Mistral AI": 4, // --s5 pink
  Cohere: 3, // --s4 amber
  Minor: 3,
  Major: 1,
  Critical: 7, // --s8 red
};

/** Any other series (a share class, "weekly count") takes the two slots nothing above owns first (teal, red), then any
 *  slot not already used on this chart. */
const UNOWNED_FIRST = [2, 7];

export function slotsFor(series: string[]): number[] {
  const fixed = series.map((s) => NAME_SLOT[s] ?? -1);
  const taken = new Set(fixed.filter((i) => i >= 0));
  const order = [...UNOWNED_FIRST, ...SLOTS.keys()];
  let next = 0;
  return fixed.map((i) => {
    if (i >= 0) return i;
    while (taken.has(order[next])) next++;
    const slot = order[next] ?? SLOTS.length; // out of slots: reads as muted
    taken.add(slot);
    return slot;
  });
}

export function useSeriesColors(spec: ChartSpec) {
  const scheme = useContext(SchemeContext);
  const series = seriesOf(spec);
  const [colors, setColors] = useState<string[]>([]);
  // Read the tokens after commit, once the theme attribute has been applied.
  useEffect(() => {
    const css = getComputedStyle(document.documentElement);
    const slots = slotsFor(series.length ? series : [""]);
    // The palette is capped at 8 slots; folding the rest into "Other" is the mart's job, so overflow reads as muted.
    setColors(slots.map((i) => css.getPropertyValue(SLOTS[i] ?? "--idle").trim() || "#898781"));
  }, [scheme, series.join("|")]); // eslint-disable-line react-hooks/exhaustive-deps -- colours derive from spec and scheme
  return { scheme, series, colors };
}

export default function ChartPlot({ spec }: { spec: ChartSpec }) {
  const { ref, width } = useWidth();
  const { series, colors } = useSeriesColors(spec);

  useEffect(() => {
    const el = ref.current;
    if (!el || !width || colors.length < Math.max(series.length, 1)) return;
    const css = getComputedStyle(document.documentElement);
    const v = (n: string) => css.getPropertyValue(n).trim();
    const plot = build(spec, series, colors, { ink: v("--ink"), ink3: v("--ink-3"), surface: v("--surface"), axis: v("--axis") }, width);
    el.replaceChildren(plot);
    return () => plot.remove();
  }, [spec, width, colors]); // eslint-disable-line react-hooks/exhaustive-deps -- colours derive from spec and scheme

  return (
    <>
      <Legend series={series} colors={colors} />
      <div ref={ref} role="img" aria-label={`${spec.title}. Use the table view for exact values.`} />
    </>
  );
}

interface Ink {
  ink: string;
  ink3: string;
  surface: string;
  axis: string;
}

function build(spec: ChartSpec, series: string[], colors: string[], c: Ink, width: number) {
  const { x, y, color } = spec.encoding;
  const xf = x!.field;
  const yf = y!.field;
  const yFmt = y!.format ?? "float";
  const temporal = x!.type === "temporal";
  const cf = color?.field;
  const fill = (r: Row) => (cf ? colors[series.indexOf(String(r[cf]))] : colors[0] || "#2a78d6");
  const stroke = fill;
  const rows = spec.rows;
  const xs = (r: Row) => (temporal ? toDate(r[xf]) : r[xf]);

  const tipText = (r: Row) =>
    spec.columns.map((col) => `${col.label}: ${fmt(r[col.field], col.format)}`).join("\n");

  const lastPerSeries = series.length
    ? series.map((s) => rows.filter((r) => String(r[cf!]) === s).at(-1)!)
    : [rows.at(-1)!];
  const vals = lastPerSeries.map((r) => Number(r[yf]));
  const span = Math.max(...rows.map((r) => Number(r[yf]))) - Math.min(...rows.map((r) => Number(r[yf]))) || 1;
  // End labels only where they can't collide; the legend and tooltip carry the rest.
  const labelEnds = vals.every((a, i) => vals.every((b, j) => i === j || Math.abs(a - b) / span > 0.1));

  const band = ["bar", "stacked_bar"].includes(spec.kind);
  const bandKeys = [...new Set(rows.map((r) => String(r[xf])))];
  const every = Math.ceil(bandKeys.length / 8);
  const xScale: Plot.ScaleOptions = band
    ? {
        type: "band",
        label: null,
        padding: bandKeys.length <= 6 ? 0.75 : 0.4,
        tickFormat: (d: string) => (bandKeys.indexOf(d) % every === 0 ? (temporal ? shortDate(d) : d) : ""),
      }
    : { label: temporal ? null : x!.label, ticks: 6 };

  const base: Plot.PlotOptions = {
    width,
    height: 320,
    marginLeft: 58,
    marginRight: !band && labelEnds && spec.kind === "line" ? 64 : 16,
    style: { background: "transparent", color: c.ink, fontFamily: "var(--sans)", fontSize: "12px", ["--plot-background" as string]: c.surface },
    x: xScale,
    y: { label: y!.label, grid: true, tickFormat: (d: number) => axis(d, yFmt), nice: true },
    color: { type: "categorical", domain: series, range: colors },
  };

  const tip = (extra: Plot.TipOptions = {}) => Plot.tip(rows, Plot.pointer({ x: xs, y: (r: Row) => r[yf], title: tipText, ...extra }) as Plot.TipOptions);
  const ends = Plot.dot(lastPerSeries, { x: xs, y: (r: Row) => r[yf], fill, r: 4, stroke: c.surface, strokeWidth: 2 });
  const endLabel = Plot.text(lastPerSeries, {
    x: xs,
    y: (r: Row) => r[yf],
    text: (r: Row) => axis(r[yf], yFmt),
    dx: 10,
    textAnchor: "start",
    fill: c.ink,
  });
  const line = Plot.line(rows, { x: xs, y: (r: Row) => r[yf], stroke, strokeWidth: 2, strokeLinejoin: "round", strokeLinecap: "round", z: cf ? (r: Row) => r[cf] : undefined });

  // A colour field that is not the x field means several series per category: draw them side by side, one group per category.
  const groupedBars = () => {
    const groups = [...new Set(rows.map((r) => String(r[xf])))]; // categories in order of appearance, not alphabetical
    const crowded = groups.length > 6;
    const place = { fx: (r: Row) => String(r[xf]), x: (r: Row) => String(r[cf!]) };
    const value = (r: Row) => Number(r[yf]);
    const text = (r: Row) => (yFmt === "pct" ? `${Math.round(value(r) * 100)}%` : axis(r[yf], yFmt)); // short, so neighbours do not collide
    const label = { ...place, y: value, text, fill: c.ink, fontSize: 10 };
    return Plot.plot({
      ...base,
      height: crowded ? 380 : 320,
      marginBottom: crowded ? 96 : 40,
      fx: { axis: null, padding: 0.2, domain: groups },
      x: { axis: null, padding: 0.1, domain: series },
      marks: [
        Plot.axisFx({ anchor: "bottom", label: null, tickSize: 0, tickRotate: crowded ? -35 : 0, textAnchor: crowded ? "end" : "middle" }),
        Plot.barY(rows, { ...place, y: value, fill, ry: 3, title: tipText }),
        Plot.ruleY([0], { stroke: c.axis }),
        Plot.text(rows.filter((r) => value(r) >= 0), { ...label, dy: -6 }),
        Plot.text(rows.filter((r) => value(r) < 0), { ...label, dy: 6, lineAnchor: "top" }),
      ],
    });
  };

  switch (spec.kind) {
    case "line":
      return Plot.plot({ ...base, marks: [line, ends, ...(labelEnds ? [endLabel] : []), tip()] });
    case "area":
      return Plot.plot({
        ...base,
        marks: [Plot.areaY(rows, { x: xs, y: (r: Row) => r[yf], fill, fillOpacity: 0.1, z: cf ? (r: Row) => r[cf] : undefined }), line, ends, tip()],
      });
    case "bar":
      if (cf && cf !== xf) return groupedBars();
      return Plot.plot({
        ...base,
        marks: [
          Plot.barY(rows, { x: (r: Row) => String(r[xf]), y: (r: Row) => r[yf], fill, ry: 4 }),
          Plot.ruleY([0], { stroke: c.axis }),
          ...(rows.length <= 14 ? [Plot.text(rows, { x: (r: Row) => String(r[xf]), y: (r: Row) => r[yf], text: (r: Row) => axis(r[yf], yFmt), dy: -8, fill: c.ink })] : []),
          Plot.tip(rows, Plot.pointerX({ x: (r: Row) => String(r[xf]), y: (r: Row) => r[yf], title: tipText }) as Plot.TipOptions),
        ],
      });
    case "stacked_bar":
      return Plot.plot({
        ...base,
        marks: [
          // The surface-coloured stroke is the 2px gap between touching segments.
          Plot.barY(rows, { x: (r: Row) => String(r[xf]), y: (r: Row) => r[yf], fill, stroke: c.surface, strokeWidth: 2, title: tipText }),
          Plot.ruleY([0], { stroke: c.axis }),
        ],
      });
    case "scatter":
    case "timeline":
      return Plot.plot({
        ...base,
        marks: [Plot.dot(rows, { x: xs, y: (r: Row) => r[yf], fill, r: 5, stroke: c.surface, strokeWidth: 2 }), tip()],
      });
    default:
      throw new Error(`No plot for kind ${spec.kind}`);
  }
}
