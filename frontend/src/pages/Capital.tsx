import ChartCard from "../components/ChartCard";

export const meta = { title: "Capital & Valuation", path: "/capital", order: 70 };

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "What have investors paid?",
    note: "Every round Anthropic announced, and the valuation against the run-rate nearest in time. Table views show the arithmetic.",
    charts: ["capital.valuation", "capital.rounds"],
  },
  {
    title: "How fast is revenue growing?",
    note: "Annualised revenue in Anthropic's own statements, with press reports kept as a separate, visibly different series.",
    charts: ["capital.run_rate"],
  },
  {
    title: "What do the secondary market and mutual funds show?",
    note: "SEC filings. The Form D vehicles are other firms' funds that hold Anthropic shares, not Anthropic's own raises. The fund marks are each fund's own estimate of one share's value.",
    charts: ["capital.form_d", "capital.fund_marks", "capital.fund_mark_changes"],
  },
  {
    title: "What does Amazon report?",
    note: "Amazon's 10-K and 10-Q passages on its convertible notes and preferred stock in Anthropic. Alphabet's filings never name Anthropic.",
    charts: ["capital.amazon"],
  },
];

export default function Capital() {
  return (
    <>
      <header className="page-head">
        <h1>Capital &amp; Valuation</h1>
        <p>
          Funding rounds, run-rate revenue and who holds Anthropic, from Anthropic's own announcements and SEC filings. Every ledger row
          quotes its source word for word. Anthropic's share count is not public, so no valuation is derived from fund marks.
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
