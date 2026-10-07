import * as Plot from "@observablehq/plot";
import { useContext, useEffect, useRef, useState } from "react";
import { axis, fmt, shortDate, toDate } from "../format";
import { SchemeContext } from "../theme";
import type { ChartSpec, Layer } from "../types";

type Row = ChartSpec["rows"][number];
const SLOTS = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7", "--s8"];

/** The field that says whether a row belongs to a layer: a layer draws only the rows where it is not null. */
const valueField = (l: Layer) => (l.mark === "band" ? l.y_low : l.mark === "rule" ? l.label : l.y)!;
const drawn = (spec: ChartSpec, l: Layer) => spec.rows.filter((r) => r[valueField(l)] != null);

/** A band, and a rule that is not split by series, are drawn in neutral ink: they have no tick box. */
const neutral = (l: Layer) => l.mark === "band" || (l.mark === "rule" && !l.series);

/** The rows a layer draws once the unticked entries are removed: the whole layer when its own name is unticked, and the
 *  rows of any unticked series of a split layer. */
function visibleRows(spec: ChartSpec, l: Layer, hidden: Set<string>) {
  if (!neutral(l) && hidden.has(l.name)) return [];
  return drawn(spec, l).filter((r) => !l.series || !hidden.has(String(r[l.series])));
}

/** Whether anything but a neutral band or rule is still drawn. */
const anyDrawn = (spec: ChartSpec, hidden: Set<string>) => (spec.layers ?? []).some((l) => !neutral(l) && visibleRows(spec, l, hidden).length);

/** The dimensions of a combined chart: layers whose name is not a company or a series value, so they stand for a different
 *  measure drawn with a different mark (a gap as bars, a second measure as dots, releases as lines). */
function comboDimensions(spec: ChartSpec): Layer[] {
  const values = new Set(comboSeries(spec));
  return (spec.layers ?? []).filter((l) => !neutral(l) && (l.series ? !values.has(l.name) : !(l.name in NAME_SLOT)));
}

/** Legend entries of a combined chart: one per layer, or one per value of a layer's series field. A band, and a rule
 *  that is not split by series, are drawn in neutral ink and stay out of the legend. */
function comboSeries(spec: ChartSpec): string[] {
  const names: string[] = [];
  for (const l of spec.layers ?? []) {
    if (neutral(l)) continue;
    for (const n of l.series ? drawn(spec, l).map((r) => String(r[l.series!])) : [l.name]) if (!names.includes(n)) names.push(n);
  }
  return names;
}

/** Series in order of first appearance, so a colour follows its entity and never its rank. */
export function seriesOf(spec: ChartSpec): string[] {
  if (spec.kind === "combo") return comboSeries(spec);
  const f = spec.encoding.color?.field;
  return f ? [...new Set(spec.rows.map((r) => String(r[f])))] : [];
}

const GLYPH: Record<string, string> = { line: "▬", bar: "▮", point: "●", rule: "┃", area: "▬" };

export interface LegendGroup {
  title: string | null;
  entries: { name: string; color?: string; mark?: string; locked: boolean }[];
}

/** The legend doubles as tick boxes, in up to two groups: the companies (or other series) and the dimensions, the different
 *  measures drawn with different marks. A ticked entry is drawn, an unticked one is not, and at least one stays ticked. */
export function Legend({ groups, hidden, onToggle }: { groups: LegendGroup[]; hidden: Set<string>; onToggle: (s: string) => void }) {
  if (!groups.length) return null;
  return (
    <div className="legend" role="group" aria-label="Shown on the chart">
      {groups.map((g) => (
        <div key={g.title ?? "all"} className="legend-group">
          {g.title && <span className="legend-title">{g.title}</span>}
          {g.entries.map((e) => {
            const on = !hidden.has(e.name);
            return (
              <label key={e.name} className={on ? "" : "off"} title={on && e.locked ? "At least one stays ticked" : undefined}>
                <input type="checkbox" checked={on} disabled={on && e.locked} onChange={() => onToggle(e.name)} />
                {e.color ? <i style={{ background: e.color }} /> : <b className="glyph">{GLYPH[e.mark ?? ""] ?? "▬"}</b>}
                {e.name}
              </label>
            );
          })}
        </div>
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
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const combo = spec.kind === "combo";
  const dimensions = combo ? comboDimensions(spec) : [];
  const dimNames = new Set(dimensions.map((l) => l.name));
  const kept = (h: Set<string>) => (combo ? anyDrawn(spec, h) : series.some((n) => !h.has(n)));
  const toggle = (name: string) =>
    setHidden((h) => {
      const next = new Set(h);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return kept(next) ? next : h;
    });
  const locked = (name: string) => !kept(new Set([...hidden, name]));
  const entries = series.filter((n) => !dimNames.has(n));
  const groups: LegendGroup[] = [];
  if (entries.length >= (dimensions.length ? 1 : 2))
    groups.push({
      title: dimensions.length || entries.every((n) => n in NAME_SLOT) ? (entries.every((n) => n in NAME_SLOT) ? "Companies" : "Series") : null,
      entries: entries.map((n) => ({ name: n, color: colors[series.indexOf(n)], locked: locked(n) })),
    });
  if (dimensions.length) groups.push({ title: "Dimensions", entries: dimensions.map((l) => ({ name: l.name, mark: l.mark, locked: locked(l.name) })) });
  // A group where every entry is locked (one series, one dimension) offers nothing to tick, so it is left out.
  const offered = groups.filter((g) => g.entries.some((e) => !e.locked || hidden.has(e.name)));

  useEffect(() => {
    const el = ref.current;
    if (!el || !width || colors.length < Math.max(series.length, 1)) return;
    const css = getComputedStyle(document.documentElement);
    const v = (n: string) => css.getPropertyValue(n).trim();
    const ink = { ink: v("--ink"), ink3: v("--ink-3"), surface: v("--surface"), axis: v("--axis") };
    // A combined chart keeps every series and skips the hidden ones layer by layer. Any other chart is drawn from the ticked
    // series only, each keeping its own colour, so a series never changes colour when another is hidden.
    const cf = spec.encoding.color?.field;
    const keep = series.map((_, i) => i).filter((i) => !hidden.has(series[i]));
    const plot =
      spec.kind === "combo"
        ? build(spec, series, colors, ink, width, hidden)
        : build(
            cf ? { ...spec, rows: spec.rows.filter((r) => !hidden.has(String(r[cf]))) } : spec,
            keep.map((i) => series[i]),
            keep.map((i) => colors[i]),
            ink,
            width,
          );
    el.replaceChildren(plot);
    return () => plot.remove();
  }, [spec, width, colors, hidden]); // eslint-disable-line react-hooks/exhaustive-deps -- colours derive from spec and scheme

  return (
    <>
      <Legend groups={offered} hidden={hidden} onToggle={toggle} />
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

function build(spec: ChartSpec, series: string[], colors: string[], c: Ink, width: number, hidden: Set<string> = new Set()) {
  if (spec.kind === "combo") return comboPlot(spec, series, colors, c, width, hidden);
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

/** Several marks over one shared x axis, so two aspects can be read against each other. Categories (an ordinal axis) are a band
 *  scale in the order of the rows; dates and numbers are a continuous scale. Marks are drawn back to front. Extra panels stack
 *  under the main one on the same x axis, each with its own y axis, and a rule runs through every panel, so one event can be read
 *  against several measures. A bar on a continuous axis is drawn as a thick stick, since the dates are not evenly spaced. */
function comboPlot(spec: ChartSpec, series: string[], colors: string[], c: Ink, width: number, hidden: Set<string>) {
  const { x, y } = spec.encoding;
  const xf = x!.field;
  const layers = spec.layers ?? [];
  const temporal = x!.type === "temporal";
  const banded = x!.type === "nominal" || x!.type === "ordinal";
  const xv = (r: Row) => (banded ? String(r[xf]) : temporal ? toDate(r[xf]) : r[xf]);
  const keys = [...new Set(spec.rows.map((r) => String(r[xf])))];
  // Names on the axis (models, pairs) are the point of the chart: up to 30 of them are all shown, tilted, rather than every few.
  const tilted = banded && keys.length > 8 && keys.length <= 30;
  const every = tilted ? 1 : Math.ceil(keys.length / 10);
  const paint = (l: Layer) => (r: Row) => colors[series.indexOf(l.series ? String(r[l.series]) : l.name)] ?? c.ink3;
  const num = (f?: string) => (r: Row) => Number(r[f!]);
  const tipText = (r: Row) => spec.columns.map((col) => `${col.label}: ${fmt(r[col.field], col.format)}`).join("\n");
  const front = ["band", "bar", "rule", "line", "point"];
  const shown = (l: Layer) => visibleRows(spec, l, hidden);
  const all = [{ label: y!.label, format: y!.format }, ...(spec.panels ?? [])];
  const inPanel = (i: number) => layers.filter((l) => (l.panel ?? 0) === i);
  // A panel is drawn while any of its own layers (not a band or a rule) has rows left after the unticking.
  const live = all.map((_, i) => i).filter((i) => inPanel(i).some((l) => !neutral(l) && shown(l).length));
  const rules = layers.filter((l) => l.mark === "rule");
  const domain = banded ? keys : (() => {
    const v = spec.rows.map((r) => (temporal ? toDate(r[xf]) : Number(r[xf]))) as (Date | number)[];
    return [v.reduce((a, b) => (a < b ? a : b)), v.reduce((a, b) => (a > b ? a : b))];
  })();

  const panel = (i: number, last: boolean, first: boolean) => {
    const marks: NonNullable<Plot.PlotOptions["marks"]> = [];
    const own = [...inPanel(i), ...(i === 0 ? [] : rules)];
    for (const l of own.sort((a, b) => front.indexOf(a.mark) - front.indexOf(b.mark))) {
      const rows = shown(l);
      if (l.mark === "band") {
        marks.push(Plot.areaY(rows, { x: xv, y1: num(l.y_low), y2: num(l.y_high), fill: c.ink3, fillOpacity: 0.15 }));
      } else if (l.mark === "bar") {
        marks.push(
          banded
            ? Plot.barY(rows, { x: xv, y: num(l.y), fill: paint(l), ry: 3 })
            : Plot.ruleX(rows, { x: xv, y1: 0, y2: num(l.y), stroke: paint(l), strokeWidth: 8, strokeLinecap: "round" }),
        );
      } else if (l.mark === "rule") {
        const stroke = l.series ? paint(l) : c.ink3;
        marks.push(Plot.ruleX(rows, { x: xv, stroke, strokeWidth: 1.5, strokeOpacity: 0.8, title: (r: Row) => String(r[l.label!]) }));
        if (first && rows.length <= 30)
          marks.push(Plot.text(rows, { x: xv, text: (r: Row) => String(r[l.label!]), frameAnchor: "top", rotate: -90, textAnchor: "end", dx: -5, fontSize: 10, fill: c.ink3 }));
      } else if (l.mark === "line") {
        marks.push(Plot.line(rows, { x: xv, y: num(l.y), stroke: paint(l), strokeWidth: 2, strokeLinejoin: "round", strokeLinecap: "round", z: l.series ? (r: Row) => String(r[l.series!]) : undefined }));
      } else {
        marks.push(Plot.dot(rows, { x: xv, y: num(l.y), fill: paint(l), r: 5, stroke: c.surface, strokeWidth: 2 }));
      }
    }
    const lead = inPanel(i).find((l) => ["line", "bar", "point"].includes(l.mark) && shown(l).length);
    marks.push(Plot.ruleY([0], { stroke: c.axis }));
    if (lead) marks.push(Plot.tip(shown(lead), Plot.pointerX({ x: xv, y: num(lead.y), title: tipText }) as Plot.TipOptions));
    const fmtY = all[i].format ?? "float";
    return Plot.plot({
      width,
      height: first ? 300 : 150,
      marginLeft: 58,
      marginRight: 16,
      marginTop: first ? 20 : 10,
      marginBottom: last ? (tilted ? 76 : 30) : 6,
      style: { background: "transparent", color: c.ink, fontFamily: "var(--sans)", fontSize: "12px", ["--plot-background" as string]: c.surface },
      x: banded
        ? { type: "band", label: null, axis: last ? "bottom" : null, domain, tickRotate: tilted ? -45 : 0, padding: keys.length <= 6 ? 0.75 : 0.3, tickFormat: (d: string) => (keys.indexOf(d) % every === 0 ? (temporal ? shortDate(d) : d) : "") }
        : { label: temporal ? null : x!.label, axis: last ? "bottom" : null, domain, ticks: 6 },
      y: { label: all[i].label, grid: true, nice: true, tickFormat: (d: number) => axis(d, fmtY) },
      marks,
    });
  };

  const drawn = live.length ? live : [0];
  const plots = drawn.map((i, n) => panel(i, n === drawn.length - 1, n === 0));
  if (plots.length === 1) return plots[0];
  const stack = document.createElement("div");
  stack.append(...plots);
  return stack;
}
