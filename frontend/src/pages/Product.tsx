import ChartCard from "../components/ChartCard";

export const meta = { title: "Product & Reliability", path: "/product", order: 50 };

const SOURCE_METHODS = [
  {
    source: "Anthropic status",
    id: "status_incidents",
    method: "Automated API · HTTP GET",
    href: "https://status.claude.com/history.json",
    request: "GET /history.json?page=N; pages backward until the collection cutoff. No POST or authentication.",
    why: "The public status feed is structured and provides historical incident pages.",
    update: "Collected daily. The collector resumes from the latest stored date and keeps each raw snapshot immutable.",
  },
  {
    source: "OpenAI status",
    id: "status_incidents",
    method: "Automated API · HTTP GET",
    href: "https://status.openai.com/api/v2/incidents.json",
    request: "GET /api/v2/incidents.json. No POST or authentication.",
    why: "The public incident.io feed is structured, but it exposes only recent incidents rather than full history.",
    update: "Collected daily. History accumulates from our snapshots because older incidents cannot be backfilled from this feed.",
  },
  {
    source: "OpenRouter rankings",
    id: "openrouter_rankings",
    method: "Automated HTML scrape · HTTP GET",
    href: "https://openrouter.ai/rankings?view=week",
    request: "GET the public weekly rankings page, then parse the author request-share table. No POST, login, API key or Playwright.",
    why: "OpenRouter's unauthenticated models API exposes model metadata, but the public author request-share comparison used here is published on the rankings page.",
    update: "Collected weekly as a current snapshot. Authors outside the displayed leaders remain unknown, never recorded as zero.",
  },
  {
    source: "OpenRouter benchmark feed",
    id: "openrouter_benchmarks",
    method: "Automated API · HTTP GET",
    href: "https://openrouter.ai/docs/api/api-reference/benchmarks/list-benchmarks",
    request: "GET /api/v1/benchmarks with a free OpenRouter API key (OPENROUTER_API_KEY). One request returns every model; limits are 30 a minute and 500 a day.",
    why: "OpenRouter relays Artificial Analysis's Intelligence, Coding and Agentic indexes and publishes its own GPQA Diamond and tau-bench results with cost per task, as structured data.",
    update: "Collected weekly as a current snapshot. Without the key the source is skipped and its charts show awaiting data.",
  },
  {
    source: "Anthropic model releases",
    id: "product_model_releases",
    method: "Hardcoded · cited ledger",
    href: "https://platform.claude.com/docs/en/release-notes/overview",
    request: "No API request. A human verifies the official announcement and records its date, model, URL and exact supporting quote.",
    why: "Announcements span release notes and news pages, and there is no stable complete historical API with the needed evidence.",
    update: "Check monthly or when a model launches; append a cited row to data/ledgers/product_model_releases.csv, then rebuild.",
  },
  {
    source: "Anthropic API prices",
    id: "product_api_prices",
    method: "Hardcoded · cited ledger",
    href: "https://platform.claude.com/docs/en/about-claude/pricing",
    request: "No API request. A human records each published input/output price with the official URL and quote.",
    why: "The pricing page is current-state documentation, not a versioned price-history API; older launch prices come from announcements.",
    update: "Check monthly and after model launches or repricing; append replacement rows to data/ledgers/product_api_prices.csv, then rebuild.",
  },
  {
    source: "Peer subscription prices",
    id: "product_peer_plan_prices",
    method: "Hardcoded · cited ledger",
    href: "https://support.claude.com/en/articles/8325606-what-is-the-pro-plan",
    request: "No API request. A human checks each company's official plan page; every ledger row carries its own page link and quote.",
    why: "The companies publish plans on different sites with no common API, and features or billing terms are not standardized.",
    update: "Check all five linked plan pages monthly; append newly dated rows to data/ledgers/product_peer_plan_prices.csv, then rebuild.",
  },
  {
    source: "Public adoption disclosures",
    id: "product_adoption_claims",
    method: "Hardcoded · cited ledger",
    href: "https://www.anthropic.com/news/anthropic-raises-30-billion-series-g-funding-380-billion-post-money-valuation",
    request: "No API request. A human records an official company disclosure with its original scope, qualifier, date, link and quote.",
    why: "These figures appear irregularly in announcements and describe different measures; automating a numeric scrape would erase their meaning.",
    update: "Review official announcements quarterly; append only clearly scoped rows to data/ledgers/product_adoption_claims.csv, then rebuild.",
  },
] as const;

interface Embed {
  title: string;
  note: string;
  href: string;
  height?: number;
  // OpenRouter sends X-Frame-Options: SAMEORIGIN (checked 2026-10-06), so its pages open only in their own tab.
  blocked?: boolean;
}

const LEADERBOARDS: Embed[] = [
  {
    title: "Artificial Analysis: model leaderboard",
    note: "Intelligence Index, price, speed and context for every tracked model.",
    href: "https://artificialanalysis.ai/leaderboards/models",
  },
  {
    title: "LiveBench: objective benchmark",
    note: "Contamination-resistant tasks scored automatically, by category.",
    href: "https://livebench.ai/#/",
  },
  {
    title: "OpenRouter: rankings",
    note: "Which models developers actually route requests to.",
    href: "https://openrouter.ai/rankings#leaderboard-table",
    blocked: true,
  },
];

const COMPARISONS: Embed[] = [
  {
    title: "Compare models: intelligence vs token use",
    note: "Artificial Analysis: how much each model scores against the tokens it spends to get there.",
    href: "https://artificialanalysis.ai/models?intelligence-index-token-use=intelligence-vs-token-use#intelligence",
  },
  {
    title: "Compare coding agents",
    note: "Artificial Analysis: coding agents ranked on the same tasks.",
    href: "https://artificialanalysis.ai/agents/coding-agents",
  },
  {
    title: "Compare image models",
    note: "Artificial Analysis: text-to-image model arena and pricing.",
    href: "https://artificialanalysis.ai/image/models",
  },
  {
    title: "Compare security: cyber index",
    note: "Artificial Analysis: how models score on cybersecurity evaluations.",
    href: "https://artificialanalysis.ai/evaluations/artificial-analysis-cyber-index",
  },
  {
    title: "AI trends",
    note: "Artificial Analysis: how quality, price and speed have moved over time.",
    href: "https://artificialanalysis.ai/trends",
  },
];

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "Who do developers route to?",
    note: "OpenRouter request share by model author: usage, not quality. It counts requests that pass through OpenRouter only.",
    charts: ["product.openrouter_share"],
  },
  {
    title: "What does OpenRouter's benchmark feed say?",
    note: "Artificial Analysis indexes and OpenRouter's own evaluations, fetched through OpenRouter's benchmarks API. Needs an OpenRouter API key to collect.",
    charts: ["product.openrouter_indexes", "product.openrouter_evals"],
  },
  {
    title: "What does it cost?",
    note: "Anthropic's API list prices by model and standard individual subscriptions for each company.",
    charts: ["product.api_prices", "product.peer_plans"],
  },
  {
    title: "What do the companies say about usage?",
    note: "Company-stated user and customer counts. Their scopes differ, so they stay in a table instead of a chart.",
    charts: ["product.public_adoption"],
  },
  {
    title: "Is the service holding up?",
    note: "Incidents each company posts on its own status page. Rising demand and rising incident counts often go together.",
    charts: ["product.incidents_monthly", "product.status_comparison"],
  },
  {
    title: "How fast is Anthropic shipping?",
    note: "Every model announcement, dated by Anthropic's own pages.",
    charts: ["product.release_cadence", "product.model_releases"],
  },
];

function EmbedCard({ title, note, href, height = 720, blocked }: Embed) {
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
        {blocked ? (
          <p className="takeaway">
            {host} does not allow its pages to be shown inside other sites, so open it with the link above.
          </p>
        ) : (
          <iframe
            src={href}
            title={title}
            loading="lazy"
            referrerPolicy="no-referrer"
            sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-forms"
            style={{ width: "100%", height, marginTop: 14, border: "1px solid var(--border)", borderRadius: 6, background: "#fff" }}
          />
        )}
      </div>
      <ul className="prov" aria-label="Sources">
        <li>
          <a href={href} target="_blank" rel="noreferrer">
            {host} ↗
          </a>
          <span className="sep">
            {blocked
              ? "link only: not collected or stored by this dashboard"
              : "loaded live from the site when you open this page · not collected or stored by this dashboard, so no as-of date of ours applies · if the frame is blank, the site refused to load; use the link"}
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
          Where the frontier models stand on live third-party leaderboards, what Claude costs against its peers, and whether
          the service is holding up. Embedded pages are the sites' own live views; charts below them are collected by us,
          with source and caveats on every number.
          <br />
          <a href="#product-source-methods">See exactly how every Product source is collected and updated ↓</a>
        </p>
      </header>
      <section>
        <h2 className="section-title">Overall leaderboards</h2>
        <div className="grid">
          {LEADERBOARDS.map((e) => (
            <EmbedCard key={e.href} {...e} />
          ))}
        </div>
      </section>
      <section>
        <h2 className="section-title">Compare models, agents, images and security</h2>
        <div className="grid">
          {COMPARISONS.map((e) => (
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
      <section id="product-source-methods">
        <h2 className="section-title">How is every Product source collected?</h2>
        <p className="subtitle" style={{ marginBottom: 14 }}>
          Playwright is used only to test this dashboard. It does not collect any Product data. Automated sources below use
          HTTP GET; “hardcoded” means a cited human-maintained ledger, not an unsupported number. The embedded leaderboards
          above are not collected at all: your browser loads them live from their owners.
        </p>
        <div className="card">
          <div className="card-body">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Source</th>
                    <th>Collection method</th>
                    <th>Request or evidence page</th>
                    <th>Why this method</th>
                    <th>How it updates</th>
                  </tr>
                </thead>
                <tbody>
                  {SOURCE_METHODS.map((source) => (
                    <tr key={`${source.id}-${source.source}`}>
                      <td>
                        <strong>{source.source}</strong>
                        <br />
                        <code>{source.id}</code>
                      </td>
                      <td>{source.method}</td>
                      <td>
                        <a href={source.href} target="_blank" rel="noreferrer">
                          {new URL(source.href).hostname} ↗
                        </a>
                        <br />
                        {source.request}
                      </td>
                      <td>{source.why}</td>
                      <td>{source.update}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
