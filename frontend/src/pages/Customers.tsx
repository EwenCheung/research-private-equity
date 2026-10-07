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
];

export default function Customers() {
  return (
    <>
      <header className="page-head">
        <h1>Customers</h1>
        <p style={{ marginBottom: 10 }}>
          <strong>What you are reading.</strong> Who is naming Claude and using it, against OpenAI, Google DeepMind, xAI, Mistral and
          Cohere: employers (job posts that name a lab) and listed companies (filings that name a lab). Anthropic is private, so
          this comes from other people's posts and filings.
        </p>
        <p>
          <strong>How to read it.</strong> More mentions over time suggest wider use. A mention is not usage or revenue, and not a
          purchase: a company that names a lab can be a customer, investor, supplier or rival. Read each lab against its own history
          first.
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
