import ChartCard from "../components/ChartCard";

export const meta = { title: "Capital & Valuation", path: "/capital", order: 70 };

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "What do funds say one Anthropic share is worth?",
    note: "Each dot is one fund's reported value per share at a reporting date. Read the trend, not a single dot.",
    charts: ["capital.fund_marks"],
  },
];

export default function Capital() {
  return (
    <>
      <header className="page-head">
        <h1>Capital &amp; Valuation</h1>
        <p style={{ marginBottom: 10 }}>
          <strong>What you are reading.</strong> What mutual funds that hold Anthropic shares say each share is worth. The source is the holdings funds report to the SEC (Form N-PORT).
        </p>
        <p>
          <strong>How to read it.</strong> Marks rising between reports mean funds are revaluing Anthropic upward. They are the funds' own estimates, not trades, and Anthropic's share count is not public, so they cannot be turned into a company valuation.
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
