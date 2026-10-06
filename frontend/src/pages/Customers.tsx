import ChartCard from "../components/ChartCard";

export const meta = { title: "Customers", path: "/customers", order: 60 };

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
];

export default function Customers() {
  return (
    <>
      <header className="page-head">
        <h1>Customers</h1>
        <p style={{ marginBottom: 10 }}>
          <strong>What you are reading.</strong> Public evidence that businesses use or depend on Claude, against OpenAI, Google DeepMind, xAI, Mistral and Cohere. Anthropic is private, so this comes from other people's job posts and from the SEC filings of listed companies.
        </p>
        <p>
          <strong>How to read it.</strong> More mentions over time suggest wider adoption. A mention is not a purchase: a company that names a lab can be a customer, investor, supplier or rival.
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
