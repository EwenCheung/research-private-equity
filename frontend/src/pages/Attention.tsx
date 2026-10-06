import ChartCard from "../components/ChartCard";

export const meta = { title: "Consumer & Attention", path: "/attention", order: 40 };

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "Is the public paying attention?",
    note: "Reader curiosity about each company's assistant, month by month.",
    charts: ["attention.wiki_products"],
  },
  {
    title: "Which labs do employers name when hiring developers?",
    note: "Hacker News's monthly 'Who is hiring?' thread, counted back to 2023. It skews to startups and developer tools.",
    charts: ["customers.hn_share"],
  },
  {
    title: "Which listed companies name each lab in their filings?",
    note: "10-K and 10-Q filings found through SEC EDGAR full-text search, counted by the quarter filed.",
    charts: ["customers.sec_filers"],
  },
];

export default function Attention() {
  return (
    <>
      <header className="page-head">
        <h1>Consumer &amp; Attention</h1>
        <p style={{ marginBottom: 10 }}>
          <strong>What you are reading.</strong> Who is paying attention to Claude and using it, against OpenAI, Google DeepMind, xAI,
          Mistral and Cohere: the public (Wikipedia views of each assistant), employers (job posts that name a lab) and listed
          companies (filings that name a lab). Anthropic is private, so this comes from other people's posts, filings and Wikipedia.
        </p>
        <p>
          <strong>How to read it.</strong> More views and mentions over time suggest wider use. Interest is not usage or revenue, and a
          mention is not a purchase: a company that names a lab can be a customer, investor, supplier or rival. ChatGPT's article draws
          far more views than Claude's, so read Claude against its own history first.
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
