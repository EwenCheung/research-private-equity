import ChartCard from "../components/ChartCard";

export const meta = { title: "Consumer & Attention", path: "/attention", order: 40 };

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "Is the public paying attention?",
    note: "Reader curiosity about each company's assistant, month by month.",
    charts: ["attention.wiki_products"],
  },
];

export default function Attention() {
  return (
    <>
      <header className="page-head">
        <h1>Consumer &amp; Attention</h1>
        <p style={{ marginBottom: 10 }}>
          <strong>What you are reading.</strong> How much the public is looking up each company's AI assistant. The source is monthly views of each assistant's Wikipedia article.
        </p>
        <p>
          <strong>How to read it.</strong> Rising views mean more curiosity. Interest is not usage or revenue, though it tends to lead and lag them. ChatGPT's article draws far more views than Claude's, so read Claude against its own history first.
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
