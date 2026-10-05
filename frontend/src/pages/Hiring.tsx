import ChartCard from "../components/ChartCard";

export const meta = { title: "Hiring & Talent", path: "/hiring", order: 20 };

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "How much they are hiring",
    note: "Roles on each company's own job board: collected daily, with monthly Internet Archive captures for the history.",
    charts: ["hiring.headline_open_roles", "hiring.open_roles"],
  },
  {
    title: "What and where",
    note: "The mix of functions and regions in the open roles, from each posting's department, title and location.",
    charts: ["hiring.role_mix", "hiring.gtm_to_rd", "hiring.region_mix"],
  },
  {
    title: "Foreign skilled hiring",
    note: "H-1B applications from Department of Labor files. They are imported by hand each quarter.",
    charts: ["hiring.h1b_filings", "hiring.h1b_wages"],
  },
];

export default function Hiring() {
  return (
    <>
      <header className="page-head">
        <h1>Hiring &amp; Talent</h1>
        <p>
          What Anthropic is hiring for, against OpenAI, xAI, Mistral and Cohere. Google DeepMind is missing: it posts only on
          Google's careers site, which has no public API.
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
