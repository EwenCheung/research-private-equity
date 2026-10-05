import { useEffect, useState } from "react";
import { api } from "../api";
import { FreshnessBadge, HardcodedBadge, ManualBadge } from "../components/Badges";
import { utc } from "../format";
import type { Freshness } from "../types";

export const meta = { title: "Data & Methods", path: "/data", order: 100 };

interface Source {
  id: string;
  page: string;
  label: string;
  url: string;
  method: "api" | "scrape" | "manual" | "ledger";
  tier: string;
  cadence: string;
  sla_days: number;
  backfillable: boolean;
  caveats: string;
  retrieved_at: string | null;
  as_of: string | null;
  row_count: number;
  readable: boolean;
  freshness: Freshness;
}

interface MartRow {
  id: string;
  page: string;
  title: string;
  status: "ok" | "awaiting_data";
}

const PAGES: Record<string, string> = {
  hiring: "Hiring & Talent",
  dev_adoption: "Developer Adoption",
  attention: "Consumer & Attention",
  product: "Product & Reliability",
  customers: "Customers & Contracts",
  capital: "Capital & Valuation",
  licensed_data: "Licensed Alt-Data",
};

const TIERS: [string, string][] = [
  ["company-stated", "The company's own channel: its job board, status page, newsroom or pricing page."],
  ["filing", "A regulatory record: SEC, Department of Labor, federal contract data."],
  ["platform", "Third-party telemetry: npm, PyPI, GitHub, Wikipedia, App Store, GDELT."],
  ["press", "A media report."],
  ["vendor", "Licensed alt-data bought from a provider (YipitData, M Science)."],
  ["derived", "Computed by us from the rows shown; charts say how."],
];

// What we do not have, and why. Collected sources that are stale or awaiting data appear in the tables above.
const GAPS: [string, string][] = [
  ["Google DeepMind hiring", "Posts only on Google's careers site, which has no public API, and files H-1B applications as Google LLC."],
  ["LinkedIn headcount", "No public API; the reference case study lists the same gap."],
  ["Reddit activity", "Needs authorised API access."],
  ["Token share by provider (OpenRouter)", "Its terms forbid scraping, and no free equivalent exists."],
  ["Google Trends", "No public API. It runs through SerpAPI and needs SERPAPI_KEY; without it the chart is empty."],
  ["YipitData and M Science", "Licensed. Entered by hand through /add-manual-data; empty until the team enters figures."],
  ["H-1B applications", "The Department of Labor blocks automated downloads. A person downloads each quarterly file and imports it."],
  ["App Store rank, news headlines, GitHub org totals, VS Code installs", "Their providers publish no history, so each series starts on the day we began collecting."],
  ["Secondary-market prices (Forge, Caplight, Hiive)", "Paid; not licensed."],
  ["Web traffic and app downloads (Similarweb, Sensor Tower)", "Paid; not licensed."],
  ["Implied valuation from fund marks", "Anthropic's share count is not public, so a mark's percentage change is shown instead."],
];

const METHOD: Record<Source["method"], string> = { api: "API", scrape: "Scrape", manual: "Manual", ledger: "Ledger" };
const isWeb = (url: string) => /^https?:\/\//.test(url) && !/[{}]/.test(url);

function SourceRow({ s }: { s: Source }) {
  const linked = isWeb(s.url);
  return (
    <tr>
      <td>
        {linked ? (
          <a href={s.url} target="_blank" rel="noreferrer">
            {s.label} ↗
          </a>
        ) : (
          <span title={s.url}>{s.label}</span>
        )}
        <details>
          <summary>Caveats</summary>
          <p style={{ margin: "6px 0 0", maxWidth: "60ch" }}>{s.caveats}</p>
        </details>
      </td>
      <td>
        {s.method === "manual" ? <ManualBadge /> : null} {METHOD[s.method]}
      </td>
      <td>{s.tier}</td>
      <td>
        {s.cadence}
        <br />
        <small>SLA {s.sla_days} d{s.backfillable ? "" : " · no backfill"}</small>
      </td>
      <td>
        {s.retrieved_at ? utc(s.retrieved_at) : "never"}
        <br />
        {s.method === "ledger" ? <HardcodedBadge /> : <FreshnessBadge state={s.freshness} />}
      </td>
      <td>{s.as_of ?? "–"}</td>
      <td className="num">{s.row_count.toLocaleString("en-US")}</td>
    </tr>
  );
}

export default function DataMethods() {
  const [sources, setSources] = useState<Source[] | null>(null);
  const [marts, setMarts] = useState<MartRow[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api<Source[]>("/api/registry"), api<MartRow[]>("/api/marts")])
      .then(([s, m]) => {
        setSources(s);
        setMarts(m);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const byPage = new Map<string, Source[]>();
  for (const s of sources ?? []) byPage.set(s.page, [...(byPage.get(s.page) ?? []), s]);
  const liveSources = (sources ?? []).filter((s) => s.method !== "ledger");
  const count = (f: Freshness) => liveSources.filter((s) => s.freshness === f).length;
  const hardcoded = (sources ?? []).filter((s) => s.method === "ledger").length;
  const empty = marts.filter((m) => m.status === "awaiting_data");

  return (
    <>
      <header className="page-head">
        <h1>Data &amp; Methods</h1>
        <p>
          Every number on this site traces to a source below, with when it was last collected or entered and what it cannot tell you.
          Freshness is recomputed from the clock each time you load this page.
        </p>
      </header>

      {error ? <p className="error">{error}</p> : null}
      {sources === null && !error ? <div className="skeleton" style={{ height: 160 }} /> : null}

      {sources ? (
        <>
          <div className="gallery" aria-label="Source freshness summary">
            {(["fresh", "aging", "stale", "never"] as Freshness[]).map((f) => (
              <span key={f}>
                <FreshnessBadge state={f} /> {count(f)}
              </span>
            ))}
            <span>
              <HardcodedBadge /> {hardcoded}
            </span>
            <span>
              {sources.length} sources · {marts.length} charts · {empty.length} awaiting data
            </span>
          </div>

          <h2 className="section-title">Every source</h2>
          <p className="subtitle" style={{ marginBottom: 14 }}>
            Freshness measures when we last collected or updated a manual feed, not the date the data describes. A source is fresh within
            its SLA, aging up to twice the SLA, and stale beyond. Cited ledgers are hardcoded and shown separately.
          </p>
          {[...byPage.entries()]
            .sort(([a], [b]) => (PAGES[a] ?? a).localeCompare(PAGES[b] ?? b))
            .map(([page, rows]) => (
              <section key={page} style={{ marginBottom: 22 }}>
                <h3 style={{ font: "500 17px/1.3 var(--serif)", margin: "0 0 8px" }}>{PAGES[page] ?? page}</h3>
                <div className="table-wrap" style={{ maxHeight: "none" }}>
                  <table>
                    <thead>
                      <tr>
                        <th>Source</th>
                        <th>Method</th>
                        <th>Tier</th>
                        <th>Cadence</th>
                        <th>Last collected / entered</th>
                        <th>Data to</th>
                        <th className="num">Rows</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((s) => (
                        <SourceRow key={s.id} s={s} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ))}

          {empty.length ? (
            <>
              <h2 className="section-title">Charts awaiting data</h2>
              <ul>
                {empty.map((m) => (
                  <li key={m.id}>{m.title}</li>
                ))}
              </ul>
            </>
          ) : null}
        </>
      ) : null}

      <h2 className="section-title">What we do not have</h2>
      <div className="table-wrap" style={{ maxHeight: "none" }}>
        <table>
          <tbody>
            {GAPS.map(([what, why]) => (
              <tr key={what}>
                <th scope="row">{what}</th>
                <td>{why}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2 className="section-title">How to read a number</h2>
      <p style={{ maxWidth: "70ch" }}>
        Each chart ends with a provenance line: the source (linked), the date the data describes, and when we retrieved or entered it. A
        MANUAL badge marks a person-updated feed; HARDCODED marks a cited ledger rather than a live feed. The line names who entered it,
        when, and the supporting evidence. A chart labelled "Arithmetic, not a model" is computed from the rows shown, with the formula
        under "Assumptions &amp; detail". Raw snapshots are never edited: a correction is a new row.
      </p>
      <div className="table-wrap" style={{ maxHeight: "none" }}>
        <table>
          <thead>
            <tr>
              <th>Source tier</th>
              <th>Meaning</th>
            </tr>
          </thead>
          <tbody>
            {TIERS.map(([tier, meaning]) => (
              <tr key={tier}>
                <th scope="row">{tier}</th>
                <td>{meaning}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
