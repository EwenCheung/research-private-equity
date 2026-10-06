import ChartCard from "../components/ChartCard";

export const meta = { title: "Consumer & Attention", path: "/attention", order: 40 };

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "Is the public looking?",
    note: "Reader curiosity about each company's assistant. Interest is not usage, but it leads and lags it.",
    charts: ["attention.wiki_products"],
  },
];

export default function Attention() {
  return (
    <>
      <header className="page-head">
        <h1>Consumer &amp; Attention</h1>
        <p>
          How much public attention Anthropic's assistant draws against OpenAI, Google DeepMind, xAI, Mistral and Cohere.
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
