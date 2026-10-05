import ChartCard from "../components/ChartCard";

export const meta = { title: "Consumer & Attention", path: "/attention", order: 40 };

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "Is the public looking?",
    note: "Reader curiosity, news coverage and app-store position. Interest is not usage, but it leads and lags it.",
    charts: [
      "attention.wiki_products",
      "attention.appstore_rank",
      "attention.search_interest",
      "attention.news_share",
      "attention.wiki_companies",
    ],
  },
  {
    title: "Is the developer community talking?",
    note: "Hacker News is where engineers and founders react first.",
    charts: ["attention.hn_stories"],
  },
  {
    title: "In the news now",
    note: "The latest headlines about Anthropic.",
    charts: ["attention.recent_coverage"],
  },
];

export default function Attention() {
  return (
    <>
      <header className="page-head">
        <h1>Consumer &amp; Attention</h1>
        <p>
          How much attention Anthropic draws against OpenAI, Google DeepMind, xAI, Mistral and Cohere, from public reading,
          searching, news and developer chatter.
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
    </>
  );
}
