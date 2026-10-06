import ChartCard from "../components/ChartCard";

export const meta = { title: "Capital & Valuation", path: "/capital", order: 70 };

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "What do mutual funds say a share is worth?",
    note: "SEC N-PORT filings. Each point is one fund's own estimate of one Anthropic share's value at a reporting date.",
    charts: ["capital.fund_marks"],
  },
];

export default function Capital() {
  return (
    <>
      <header className="page-head">
        <h1>Capital &amp; Valuation</h1>
        <p>
          What mutual funds holding Anthropic shares report them to be worth. Anthropic's share count is not public, so no
          company valuation is derived from fund marks.
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
