import ChartCard from "../components/ChartCard";

export const meta = { title: "Hiring & Talent", path: "/hiring", order: 20 };

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "Is Anthropic still building out?",
    note: "Open roles on each company's job board, and how much of the hiring faces customers rather than research or engineering. The direction matters more than the level.",
    charts: ["hiring.open_roles", "hiring.gtm_to_rd"],
  },
];

export default function Hiring() {
  return (
    <>
      <header className="page-head">
        <h1>Hiring &amp; Talent</h1>
        <p style={{ marginBottom: 10 }}>
          <strong>What you are reading.</strong> How fast Anthropic is adding people, and how much of that hiring faces customers, against OpenAI, xAI, Mistral and Cohere. The source is each company's own public job board.
        </p>
        <p>
          <strong>How to read it.</strong> A rising line means a company is building out. A high customer-facing ratio means it is selling harder than it is researching. Roles posted are not roles filled. Google DeepMind is missing: its jobs sit on Google's careers site, which has no public feed.
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
