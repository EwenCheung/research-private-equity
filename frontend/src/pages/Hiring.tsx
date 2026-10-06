import ChartCard from "../components/ChartCard";

export const meta = { title: "Hiring & Talent", path: "/hiring", order: 20 };

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "Headcount build-out",
    note: "Roles on each company's own job board (daily collection, Internet Archive captures for the history), and how many face customers rather than research or engineering.",
    charts: ["hiring.open_roles", "hiring.gtm_to_rd"],
  },
];

export default function Hiring() {
  return (
    <>
      <header className="page-head">
        <h1>Hiring &amp; Talent</h1>
        <p>
          How fast Anthropic is building out, against OpenAI, xAI, Mistral and Cohere. Google DeepMind is missing: it posts only
          on Google's careers site, which has no public API.
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
