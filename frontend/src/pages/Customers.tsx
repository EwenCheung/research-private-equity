import ChartCard from "../components/ChartCard";

export const meta = { title: "Customers & Contracts", path: "/customers", order: 60 };

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "Who is building with Claude?",
    note: "Job posts in Hacker News's monthly 'Who is hiring?' thread that name a lab or its models. A free, backfilled read on developer demand, skewed to startups.",
    charts: ["customers.hn_share", "customers.hn_terms"],
  },
  {
    title: "Which public companies name Anthropic?",
    note: "10-K and 10-Q filings from SEC EDGAR full-text search. A mention is not a purchase: filers can be customers, investors, suppliers or rivals.",
    charts: ["customers.sec_filers", "customers.sec_sectors"],
  },
  {
    title: "Contracts and what Anthropic says",
    note: "Federal awards under the companies' own names, and the customer numbers Anthropic has stated, quoted from its newsroom.",
    charts: ["customers.federal_awards", "customers.kpi_claims"],
  },
];

export default function Customers() {
  return (
    <>
      <header className="page-head">
        <h1>Customers &amp; Contracts</h1>
        <p>
          Public evidence of who uses Claude, against OpenAI, Google DeepMind, xAI, Mistral and Cohere. Anthropic is private and
          files nothing with the SEC, so its own customer numbers are company claims; the rest comes from other people's posts,
          filings and government records.
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
