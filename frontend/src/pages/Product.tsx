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
    source: "Arena text leaderboard",
    id: "arena_text_leaderboard",
    method: "Automated HTML scrape · HTTP GET",
    href: "https://arena.ai/leaderboard/text?styleControl=off",
    request: "GET the public leaderboard page, then parse its server-rendered table. No POST, login, API key or Playwright.",
    why: "The public table puts score, votes, token prices and context on the same model row; no stable public endpoint used here exposes that combined view.",
    update: "Collected weekly as a current snapshot. Parser tests fail visibly if Arena changes the table columns.",
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
    source: "Artificial Analysis model leaderboard",
    id: "artificial_analysis_leaderboard",
    method: "Automated HTML scrape · HTTP GET",
    href: "https://artificialanalysis.ai/leaderboards/models/",
    request: "GET the public model-leaderboard page and parse its server-rendered table. No POST, login, API key or Playwright.",
    why: "Its public table puts Intelligence Index, measured benchmark-task cost, throughput, first-chunk latency, total response time and context on one model row. The separate Data API requires an API key and is not used.",
    update: "Collected weekly as a current snapshot. The live page exposes no separate data date, so as-of is the retrieval date; parser tests fail visibly if the table changes.",
  },
  {
    source: "LiveBench leaderboard and cost files",
    id: "livebench_leaderboard",
    method: "Automated structured data · HTTP GET",
    href: "https://livebench.ai/#/",
    request: "GET https://api.github.com/repos/LiveBench/new-livebench/contents/public?ref=main to find the newest complete release; then GET https://livebench.ai/table_YYYY_MM_DD.csv, categories_YYYY_MM_DD.json and cost_YYYY_MM_DD.csv. No POST, login, API key or Playwright.",
    why: "LiveBench publishes the exact objective subtask scores, question counts, token use, prices and cost fields behind its UI as versioned files. The dashboard can reproduce category and overall scores without scraping rendered cells.",
    update: "Checked weekly. Each observation uses the release date, while immutable raw snapshots preserve what the versioned files contained when collected.",
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
    source: "Claude plan prices",
    id: "product_plan_prices",
    method: "Hardcoded · cited ledger",
    href: "https://claude.com/pricing",
    request: "No API request. A human records the displayed plan price, billing basis, official URL and quote.",
    why: "The interactive pricing page has no stable public price-history endpoint and plan definitions can change.",
    update: "Check monthly; append a newly dated row to data/ledgers/product_plan_prices.csv, then rebuild.",
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

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "How do the frontier products compare?",
    note: "A like-for-like model snapshot from Arena: human-preference score, list price and published context window for the same model entry.",
    charts: ["product.frontier_scorecard", "product.price_vs_quality", "product.context_windows"],
  },
  {
    title: "What does usage cost?",
    note: "API list prices, transparent fixed-token workload estimates and standard individual subscriptions. Workload costs are per attempt, not success-adjusted.",
    charts: ["product.task_costs", "product.peer_plans", "product.api_prices", "product.plans"],
  },
  {
    title: "Who is using the products?",
    note: "OpenRouter supplies comparable platform request share. Company user and customer disclosures are kept in a separate table because their scopes differ.",
    charts: ["product.openrouter_share", "product.public_adoption"],
  },
  {
    title: "Is the service holding up?",
    note: "Incidents Anthropic posts on its own status page. Rising demand and rising incident counts often go together.",
    charts: ["product.incidents_monthly", "product.incident_hours", "product.status_comparison", "product.recent_incidents"],
  },
  {
    title: "How fast is Anthropic shipping?",
    note: "Every model announcement, dated by Anthropic's own pages.",
    charts: ["product.release_cadence", "product.model_releases"],
  },
  {
    title: "What is the combined model signal?",
    note: "LiveBench's objective leaderboard and success-adjusted task cost, then a strict same-model join across Arena, Artificial Analysis and LiveBench with company-level OpenRouter usage. Missing measurements stay blank.",
    charts: [
      "product.livebench_leaderboard",
      "product.livebench_cost_quality",
      "product.benchmark_cost_quality",
      "product.combined_model_signal",
    ],
  },
];

export default function Product() {
  return (
    <>
      <header className="page-head">
        <h1>Product &amp; Reliability</h1>
        <p>
          Compare Anthropic with OpenAI, Google, xAI, Mistral and Cohere on model quality, context, API workload cost,
          measured benchmark-task cost, latency, subscriptions and usage—then track Claude's reliability and release pace.
          Every number keeps its source and caveats.
          <br />
          <a href="#product-source-methods">See exactly how every Product source is collected and updated ↓</a>
        </p>
      </header>
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
          HTTP GET; “hardcoded” means a cited human-maintained ledger, not an unsupported number.
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
