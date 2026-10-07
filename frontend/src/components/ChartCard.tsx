import { useEffect, useState } from "react";
import { api } from "../api";
import { csvCell, fmt, isNumeric, sgt } from "../format";
import type { ChartSpec, Column, SourceLine } from "../types";
import { ArithmeticBadge, ExtrapolationBadge, FreshnessBadge, HardcodedBadge, ManualBadge } from "./Badges";
import ChartPlot from "./Plot";

export default function ChartCard({ id }: { id: string }) {
  const [spec, setSpec] = useState<ChartSpec | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<ChartSpec>(`/api/marts/${id}`).then(setSpec, (e: Error) => setError(e.message));
  }, [id]);

  if (error)
    return (
      <article className="card">
        <div className="card-body">
          <h3>{id}</h3>
          <p className="error">Couldn't load this chart: {error}</p>
        </div>
      </article>
    );
  if (!spec)
    return (
      <article className="card" aria-busy="true">
        <div className="card-body">
          <div className="skeleton" />
        </div>
      </article>
    );
  return <Card spec={spec} />;
}

function Card({ spec }: { spec: ChartSpec }) {
  const awaiting = spec.status === "awaiting_data";
  const canChart = spec.kind !== "table" && spec.kind !== "stat";
  const [view, setView] = useState<"chart" | "table">(canChart ? "chart" : "table");
  const showTable = view === "table" || spec.kind === "table";

  return (
    <article className="card">
      <div className="card-body">
        <div className="card-top">
          <div>
            <h3>{spec.title}</h3>
            {spec.subtitle && <p className="subtitle">{spec.subtitle}</p>}
          </div>
          <div className="tools">
            {canChart && !awaiting && (
              <div className="seg" role="group" aria-label="View">
                <button aria-pressed={view === "chart"} onClick={() => setView("chart")}>
                  Chart
                </button>
                <button aria-pressed={view === "table"} onClick={() => setView("table")}>
                  Table
                </button>
              </div>
            )}
            <button className="tool" disabled={awaiting} onClick={() => downloadCsv(spec)} title="Download the rows as CSV">
              CSV
            </button>
          </div>
        </div>

        {spec.badges.length > 0 && (
          <div className="badges">
            {spec.badges.includes("arithmetic") && <ArithmeticBadge />}
            {spec.badges.includes("extrapolation") && <ExtrapolationBadge />}
          </div>
        )}

        {spec.takeaway.length > 0 && (
          <p className="takeaway">
            {spec.takeaway.map((t, i) => (
              <span key={i}>{i > 0 && " "}{t}</span>
            ))}
          </p>
        )}

        <div className="viz">
          {awaiting ? (
            <Awaiting spec={spec} />
          ) : spec.kind === "stat" ? (
            <Stats spec={spec} />
          ) : showTable ? (
            <DataTable spec={spec} />
          ) : (
            <ChartPlot spec={spec} />
          )}
        </div>
      </div>

      <ul className="prov" aria-label="Sources">
        {spec.sources.map((s) => (
          <li key={s.source}>
            <Provenance s={s} />
          </li>
        ))}
      </ul>

      <details className="detail">
        <summary>Assumptions &amp; detail</summary>
        {spec.assumptions.length > 0 && (
          <ul>
            {spec.assumptions.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        )}
        <dl>
          <dt>Chart</dt>
          <dd>{spec.id}</dd>
          <dt>Data as of</dt>
          <dd>{spec.as_of ?? "no data yet"}</dd>
          <dt>Built</dt>
          <dd>{sgt(spec.generated_at)}</dd>
          {spec.sources.map((s) => (
            <SourceDetail key={s.source} s={s} />
          ))}
        </dl>
      </details>
    </article>
  );
}

const isWeb = (u: string) => /^https?:\/\//.test(u) && !/[{}]/.test(u);

function Provenance({ s }: { s: SourceLine }) {
  const isHandEntered = s.method === "manual" || s.method === "ledger";
  const label = isWeb(s.url) ? (
    <a href={s.url} target="_blank" rel="noreferrer">
      {s.label} ↗
    </a>
  ) : (
    <span title={s.url}>{s.label}</span>
  );
  const asOf = <span className="sep">{s.as_of ? `data as of ${s.as_of}` : "no data yet"}</span>;

  if (isHandEntered)
    return (
      <>
        {s.method === "ledger" ? <HardcodedBadge /> : <ManualBadge />}
        {s.manual ? (
          <>
            <span>
              updated {s.manual.entered_at.slice(0, 10)} by {s.manual.entered_by}
            </span>
            <span className="sep">{s.manual.evidence}</span>
          </>
        ) : (
          <span>{label}, awaiting manual entry</span>
        )}
        {s.manual && asOf}
        {s.method === "manual" && (
          <span className="sep">
            <FreshnessBadge state={s.freshness} />
          </span>
        )}
      </>
    );
  return (
    <>
      {label}
      {asOf}
      <span className="sep">{s.retrieved_at ? `retrieved ${sgt(s.retrieved_at)}` : "not retrieved yet"}</span>
      <span className="sep">
        <FreshnessBadge state={s.freshness} />
      </span>
    </>
  );
}

function SourceDetail({ s }: { s: SourceLine }) {
  return (
    <>
      <dt>{s.label}</dt>
      <dd>
        {s.tier} · {s.method} · {s.cadence}
        {s.url.startsWith("urn:") && <> · {s.url}</>}
      </dd>
    </>
  );
}

function Awaiting({ spec }: { spec: ChartSpec }) {
  const names = spec.sources.map((s) => s.label).join(", ");
  return (
    <div className="awaiting">
      <div>
        <strong>Awaiting data</strong>
        Nothing has been collected or entered for {names} yet. This chart fills in once it has.
      </div>
    </div>
  );
}

function Stats({ spec }: { spec: ChartSpec }) {
  const y = spec.encoding.y!;
  const label = spec.columns.find((c) => c.format === "text");
  const deltas = spec.columns.filter((c) => c.field !== y.field && isNumeric(c.format));
  return (
    <div className="stats">
      {spec.rows.map((r, i) => (
        <div key={i}>
          <div className="stat-label">
            {label ? String(r[label.field]) : y.label}
          </div>
          <div className="stat-value">{fmt(r[y.field], y.format)}</div>
          <div className="stat-delta">
            {y.label}
            {deltas.map((d) => (
              <span key={d.field}> · {d.label}: {signed(r[d.field], d)}</span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

const signed = (v: string | number | null, c: Column) => (Number(v) > 0 ? "+" : "") + fmt(v, c.format);

function DataTable({ spec }: { spec: ChartSpec }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {spec.columns.map((c) => (
              <th key={c.field} className={isNumeric(c.format) ? "num" : ""}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {spec.rows.map((r, i) => (
            <tr key={i}>
              {spec.columns.map((c) => (
                <td key={c.field} className={isNumeric(c.format) ? "num" : ""}>
                  <Cell value={r[c.field]} column={c} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Cell({ value, column }: { value: string | number | null; column: Column }) {
  if (column.format === "url" && typeof value === "string" && isWeb(value))
    return (
      <a href={value} target="_blank" rel="noreferrer">
        {new URL(value).hostname} ↗
      </a>
    );
  return <>{fmt(value, column.format)}</>;
}

/** Raw values in column order, so the file re-imports cleanly; the card's provenance stays on screen. */
function downloadCsv(spec: ChartSpec) {
  const head = spec.columns.map((c) => csvCell(c.field)).join(",");
  const body = spec.rows.map((r) => spec.columns.map((c) => csvCell(r[c.field])).join(","));
  const blob = new Blob([[head, ...body].join("\n") + "\n"], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${spec.id}${spec.as_of ? `_asof-${spec.as_of}` : ""}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}
