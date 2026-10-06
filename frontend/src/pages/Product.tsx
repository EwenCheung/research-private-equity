import ChartCard from "../components/ChartCard";

export const meta = { title: "Product & Reliability", path: "/product", order: 50 };

interface Embed {
  title: string;
  note: string;
  href: string;
  height?: number;
}

const LEADERBOARDS: Embed[] = [
  {
    title: "Artificial Analysis: model leaderboard",
    note: "Intelligence Index, price, speed and context for every tracked model.",
    href: "https://artificialanalysis.ai/leaderboards/models",
  },
  {
    title: "Artificial Analysis: coding agents",
    note: "Coding agents ranked on the same tasks.",
    href: "https://artificialanalysis.ai/agents/coding-agents",
  },
];

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "Who do developers route to?",
    note: "OpenRouter request share by model author: usage, not quality. It counts requests that pass through OpenRouter only.",
    charts: ["product.openrouter_share"],
  },
  {
    title: "How good are the models?",
    note: "Artificial Analysis's Intelligence Index, fetched through OpenRouter's benchmarks API.",
    charts: ["product.openrouter_indexes"],
  },
  {
    title: "Is the service holding up?",
    note: "Incidents Anthropic posts on its own status page. Rising demand and rising incident counts often go together.",
    charts: ["product.incidents_monthly"],
  },
];

function EmbedCard({ title, note, href, height = 720 }: Embed) {
  const host = new URL(href).hostname;
  return (
    <article className="card">
      <div className="card-body">
        <div className="card-top">
          <div>
            <h3>{title}</h3>
            <p className="subtitle">{note}</p>
          </div>
          <div className="tools">
            <a className="tool" href={href} target="_blank" rel="noreferrer">
              Open ↗
            </a>
          </div>
        </div>
        <div className="badges">
          <span className="badge">LIVE EMBED · third party</span>
        </div>
        <iframe
          src={href}
          title={title}
          loading="lazy"
          referrerPolicy="no-referrer"
          sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-forms"
          style={{ width: "100%", height, marginTop: 14, border: "1px solid var(--border)", borderRadius: 6, background: "#fff" }}
        />
      </div>
      <ul className="prov" aria-label="Sources">
        <li>
          <a href={href} target="_blank" rel="noreferrer">
            {host} ↗
          </a>
          <span className="sep">
            loaded live from the site when you open this page · not collected or stored by this dashboard, so no as-of date of
            ours applies · if the frame is blank, the site refused to load; use the link
          </span>
        </li>
      </ul>
    </article>
  );
}

export default function Product() {
  return (
    <>
      <header className="page-head">
        <h1>Product &amp; Reliability</h1>
        <p>
          Where the frontier models stand, who developers route requests to, and whether the service is holding up. Embedded
          pages are the sites' own live views; charts are collected by us, with source and caveats on every number.
        </p>
      </header>
      <section>
        <h2 className="section-title">Leaderboards</h2>
        <div className="grid">
          {LEADERBOARDS.map((e) => (
            <EmbedCard key={e.href} {...e} />
          ))}
        </div>
      </section>
      {SECTIONS.map((s) => (
        <section key={s.title}>
          <h2 className="section-title">{s.title}</h2>
          <p className="subtitle" style={{ marginBottom: 14 }}>
            {s.note}
          </p>
          <div className="grid">
            {s.charts.map((id) => (
              <ChartCard key={id} id={id} />
            ))}
          </div>
        </section>
      ))}
    </>
  );
}
