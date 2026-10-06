import ChartCard from "../components/ChartCard";

export const meta = { title: "Customers", path: "/customers", order: 40 };

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
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
  {
    title: "Is the public paying attention?",
    note: "Reader curiosity about each company's assistant, month by month.",
    charts: ["attention.wiki_products"],
  },
];

export default function Customers() {
  return (
    <>
      <header className="page-head">
        <h1>Customers</h1>
        <p style={{ marginBottom: 10 }}>
          <strong>What you are reading.</strong> Public evidence of who uses Claude, against OpenAI, Google DeepMind, xAI, Mistral
          and Cohere: businesses (employers and listed companies that name a lab) and consumers (how often people look up its
          assistant). Anthropic is private, so this comes from other people's job posts, SEC filings and Wikipedia.
        </p>
        <p>
          <strong>How to read it.</strong> More mentions and views over time suggest wider use. A mention is not a purchase: a company
          that names a lab can be a customer, investor, supplier or rival. Interest is not usage or revenue. ChatGPT's article draws
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
