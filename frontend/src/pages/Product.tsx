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
    title: "Is the service holding up?",
    note: "Incidents Claude and OpenAI each post on their own status pages, counted the same way. Rising demand and rising incident counts often go together.",
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
        <p style={{ marginBottom: 10 }}>
          <strong>What you are reading.</strong> How Claude's models rank against rivals, and how dependable the service has been. The
          rankings are the leaderboard sites' own live pages; the incident counts come from Claude's and OpenAI's own status pages.
        </p>
        <p>
          <strong>How to read it.</strong> Higher on a leaderboard is a better model. Fewer incidents is better, but read them against
          demand: outages tend to rise with usage. The embedded leaderboards are not our data.
        </p>
      </header>
      <section>
        <h2 className="section-title">How do the models rank?</h2>
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
