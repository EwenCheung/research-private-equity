import ChartCard from "../components/ChartCard";

export const meta = { title: "Product & Reliability", path: "/product", order: 50 };

const SECTIONS: { title: string; note: string; charts: string[] }[] = [
  {
    title: "Is the service holding up?",
    note: "Incidents Anthropic posts on its own status page. Rising demand and rising incident counts often go together.",
    charts: ["product.incidents_monthly", "product.incident_hours", "product.status_comparison", "product.recent_incidents"],
  },
  {
    title: "How fast is Anthropic shipping?",
    note: "Every model announcement, dated by Anthropic's own pages.",
    charts: ["product.release_cadence", "product.model_releases"],
  },
  {
    title: "What does it cost?",
    note: "API list prices per million tokens at each release, and what the consumer and team plans cost.",
    charts: ["product.api_prices", "product.plans"],
  },
];

export default function Product() {
  return (
    <>
      <header className="page-head">
        <h1>Product &amp; Reliability</h1>
        <p>
          Anthropic's reliability, release pace and pricing, from its own status page, release notes and pricing pages. Each
          ledger row quotes its source word for word.
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
